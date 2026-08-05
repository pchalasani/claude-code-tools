"""Move a Claude or Codex session between two account config dirs.

An "account" here is a config directory such as ``~/.claude`` vs
``~/.claude-rja`` (or ``~/.codex`` vs ``~/.codex-rja``) — typically
selected via the ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` env vars or a
shell alias. Moving a session keeps the same project (cwd), so the
transcript is copied verbatim into the same relative location in the
target home:

- Claude: ``projects/<encoded-path>/<uuid>.jsonl`` plus its sidecar
  directory (subagents, tool-results, workflows).
- Codex: ``sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` plus the
  session's thread-name entries in ``session_index.jsonl``.

Nothing inside the transcript needs rewriting: the jsonl carries no
account identity, only the cwd, which is unchanged by an account move.
"""

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from claude_code_tools.find_claude_session import get_custom_title
from claude_code_tools.resolve_session_names import codex_thread_names
from claude_code_tools.session_utils import get_session_uuid


@dataclass
class SessionCandidate:
    """A session transcript matched during resolution.

    Attributes:
        path: Absolute path to the session ``.jsonl`` transcript.
        session_id: Session UUID (the file stem).
        title: User-assigned name from ``/rename``, if any.
    """

    path: Path
    session_id: str
    title: str


@dataclass
class MoveResult:
    """Outcome of a completed account move.

    Attributes:
        source_file: Original transcript path in the source home.
        dest_file: New transcript path in the target home.
        sidecar_moved: True if a sidecar dir was moved alongside.
        session_id: Session UUID.
        cwd: Project directory recorded in the transcript.
        kept_source: True if the source copy was left in place.
    """

    source_file: Path
    dest_file: Path
    sidecar_moved: bool
    session_id: str
    cwd: str
    kept_source: bool
    agent: str = "claude"
    index_entry_moved: bool = False


def detect_home_kind(home: Path) -> Optional[str]:
    """Classify a config dir as a Claude or Codex home.

    Claude homes contain ``projects/``; Codex homes contain
    ``sessions/``. A dir with both (or neither) is ambiguous.

    Args:
        home: Candidate config directory.

    Returns:
        'claude', 'codex', or None if ambiguous/unrecognized.
    """
    home = home.expanduser()
    is_claude = (home / "projects").is_dir()
    is_codex = (home / "sessions").is_dir()
    if is_claude and not is_codex:
        return "claude"
    if is_codex and not is_claude:
        return "codex"
    return None


def _iter_session_files(home: Path) -> List[Path]:
    """List session transcripts in a Claude home.

    Args:
        home: Claude config dir (contains ``projects/``).

    Returns:
        All ``<uuid>.jsonl`` transcripts, excluding ``agent-*`` subagent
        files.
    """
    projects = home / "projects"
    if not projects.is_dir():
        return []
    return [
        f
        for f in projects.glob("*/*.jsonl")
        if not f.name.startswith("agent-")
    ]


def _session_title(path: Path) -> str:
    """Return the user-assigned name of a transcript, or ''."""
    return get_custom_title(path.stem, "", session_file=path)


def _tiered_match(
    candidates: List[SessionCandidate], query: str
) -> List[SessionCandidate]:
    """Pick candidates by tiered matching; first non-empty tier wins.

    Tiers: exact session id, exact name, id prefix, id substring,
    name substring. All comparisons are case-insensitive, so a pasted
    uppercase UUID still resolves. An exact name outranks an id prefix
    so a short name can never silently select a session whose UUID
    happens to start with the same characters.

    Args:
        candidates: All sessions in the searched home.
        query: Session id (full or partial) or session name.

    Returns:
        Matching candidates; empty if nothing matched.
    """
    tiers: List[List[SessionCandidate]] = [[], [], [], [], []]
    query_cf = query.casefold()
    for cand in candidates:
        sid = cand.session_id.casefold()
        title = cand.title.casefold()
        if sid == query_cf:
            tiers[0].append(cand)
        elif title == query_cf:
            tiers[1].append(cand)
        elif sid.startswith(query_cf):
            tiers[2].append(cand)
        elif query_cf in sid:
            tiers[3].append(cand)
        elif title and query_cf in title:
            tiers[4].append(cand)
    for tier in tiers:
        if tier:
            return tier
    return []


def find_sessions_in_home(home: Path, query: str) -> List[SessionCandidate]:
    """Resolve a session query against one Claude home.

    Args:
        home: Claude config dir to search.
        query: Session UUID (full or partial) or session name
            (assigned via /rename).

    Returns:
        Matching candidates; empty if nothing matched.
    """
    candidates = [
        SessionCandidate(path=f, session_id=f.stem, title=_session_title(f))
        for f in _iter_session_files(home)
    ]
    return _tiered_match(candidates, query)


def find_codex_sessions_in_home(
    home: Path, query: str
) -> List[SessionCandidate]:
    """Resolve a session query against one Codex home.

    Codex rollout files live under ``sessions/YYYY/MM/DD/`` and carry
    the session UUID as the last 36 chars of the stem; user-assigned
    thread names come from ``session_index.jsonl``.

    Args:
        home: Codex config dir to search.
        query: Session UUID (full or partial) or thread name.

    Returns:
        Matching candidates; empty if nothing matched.
    """
    home = home.expanduser()
    sessions_dir = home / "sessions"
    if not sessions_dir.is_dir():
        return []
    names = codex_thread_names(home)
    candidates = []
    for f in sessions_dir.rglob("rollout-*.jsonl"):
        uuid = get_session_uuid(f.stem)
        candidates.append(
            SessionCandidate(
                path=f,
                session_id=uuid,
                title=names.get(uuid.casefold(), ""),
            )
        )
    return _tiered_match(candidates, query)


def _transcript_cwd(session_file: Path) -> str:
    """Return the first cwd recorded in a transcript, or ''.

    Handles both Claude lines (top-level ``cwd``) and Codex lines
    (``cwd`` inside the ``payload`` of ``session_meta`` records).
    """
    import json

    try:
        with session_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("cwd"):
                    return str(data["cwd"])
                payload = data.get("payload")
                if isinstance(payload, dict) and payload.get("cwd"):
                    return str(payload["cwd"])
    except OSError:
        pass
    return ""


def move_session_between_homes(
    session_file: Path,
    from_home: Path,
    to_home: Path,
    keep: bool = False,
) -> MoveResult:
    """Move one session transcript (plus sidecar dir) to another home.

    The transcript keeps its relative location under ``projects/`` so
    the target account sees it in the same project. The sidecar dir
    (``projects/<enc>/<uuid>/`` with subagents, tool-results,
    workflows) is moved with it when present.

    Args:
        session_file: Transcript path inside ``from_home``.
        from_home: Source Claude config dir.
        to_home: Target Claude config dir.
        keep: If True, copy without removing the source.

    Returns:
        A MoveResult describing what was done.

    Raises:
        ValueError: If the session is not inside from_home, or the
            destination already has this session.
    """
    session_file = session_file.resolve()
    from_home = from_home.expanduser().resolve()
    to_home = to_home.expanduser().resolve()

    try:
        rel = session_file.relative_to(from_home)
    except ValueError as exc:
        raise ValueError(
            f"Session {session_file} is not inside source home {from_home}"
        ) from exc

    dest_file = to_home / rel
    if dest_file.exists():
        raise ValueError(
            f"Session already exists in target account: {dest_file}"
        )

    sidecar_src = session_file.with_suffix("")
    sidecar_dest = dest_file.with_suffix("")
    if sidecar_src.is_dir() and sidecar_dest.exists():
        raise ValueError(
            f"Sidecar dir already exists in target account: {sidecar_dest}"
        )

    dest_file.parent.mkdir(parents=True, exist_ok=True)
    size_before = session_file.stat().st_size
    shutil.copy2(session_file, dest_file)
    if (
        dest_file.stat().st_size != size_before
        or session_file.stat().st_size != size_before
    ):
        dest_file.unlink()
        raise ValueError(
            f"Copy verification failed for {dest_file} — the source "
            "changed during the copy (session still running?); "
            "source untouched"
        )

    sidecar_moved = False
    if sidecar_src.is_dir():
        shutil.copytree(sidecar_src, sidecar_dest)
        sidecar_moved = True

    cwd = _transcript_cwd(dest_file)

    if not keep:
        session_file.unlink()
        if sidecar_moved:
            shutil.rmtree(sidecar_src)

    return MoveResult(
        source_file=session_file,
        dest_file=dest_file,
        sidecar_moved=sidecar_moved,
        session_id=session_file.stem,
        cwd=cwd,
        kept_source=keep,
    )


def _transfer_index_entries(
    from_home: Path, to_home: Path, session_id: str, keep: bool
) -> bool:
    """Move a session's thread-name entries between Codex homes.

    Appends the session's raw ``session_index.jsonl`` lines to the
    target index; unless ``keep``, rewrites the source index without
    them.

    Args:
        from_home: Source Codex home.
        to_home: Target Codex home.
        session_id: Session UUID whose entries to transfer.
        keep: If True, leave the source index untouched.

    Returns:
        True if any entries were transferred.
    """
    import json

    src_index = from_home / "session_index.jsonl"
    if not src_index.is_file():
        return False
    matched: List[str] = []
    remaining: List[str] = []
    with src_index.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                remaining.append(line)
                continue
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and entry["id"].strip().casefold() == session_id.casefold()
            ):
                matched.append(line if line.endswith("\n") else line + "\n")
            else:
                remaining.append(line)
    if not matched:
        return False
    dest_index = to_home / "session_index.jsonl"
    # Guard against a target index whose last record lacks a trailing
    # newline: appending directly would concatenate two JSON records.
    needs_newline = False
    if dest_index.is_file() and dest_index.stat().st_size > 0:
        with dest_index.open("rb") as raw:
            raw.seek(-1, 2)
            needs_newline = raw.read(1) != b"\n"
    with dest_index.open("a", encoding="utf-8") as handle:
        if needs_newline:
            handle.write("\n")
        handle.writelines(matched)
    if not keep:
        tmp = src_index.with_name(src_index.name + ".tmp")
        tmp.write_text("".join(remaining), encoding="utf-8")
        os.replace(tmp, src_index)
    return True


def move_codex_session_between_homes(
    session_file: Path,
    from_home: Path,
    to_home: Path,
    keep: bool = False,
) -> MoveResult:
    """Move one Codex rollout file to another Codex home.

    The rollout keeps its date-based relative location under
    ``sessions/``, and the session's thread-name entries move with it
    in ``session_index.jsonl``.

    Args:
        session_file: Rollout path inside ``from_home``.
        from_home: Source Codex home.
        to_home: Target Codex home.
        keep: If True, copy without removing the source.

    Returns:
        A MoveResult describing what was done.

    Raises:
        ValueError: If the session is not inside from_home, or the
            destination already has this session.
    """
    session_file = session_file.resolve()
    from_home = from_home.expanduser().resolve()
    to_home = to_home.expanduser().resolve()

    try:
        rel = session_file.relative_to(from_home)
    except ValueError as exc:
        raise ValueError(
            f"Session {session_file} is not inside source home {from_home}"
        ) from exc

    session_id = get_session_uuid(session_file.stem)
    dest_file = to_home / rel
    if dest_file.exists():
        raise ValueError(
            f"Session already exists in target account: {dest_file}"
        )
    clash = next(
        (to_home / "sessions").rglob(f"rollout-*{session_id}.jsonl"), None
    ) if (to_home / "sessions").is_dir() else None
    if clash is not None:
        raise ValueError(
            f"Session {session_id} already exists in target account: {clash}"
        )

    dest_file.parent.mkdir(parents=True, exist_ok=True)
    size_before = session_file.stat().st_size
    shutil.copy2(session_file, dest_file)
    if (
        dest_file.stat().st_size != size_before
        or session_file.stat().st_size != size_before
    ):
        dest_file.unlink()
        raise ValueError(
            f"Copy verification failed for {dest_file} — the source "
            "changed during the copy (session still running?); "
            "source untouched"
        )

    index_moved = _transfer_index_entries(
        from_home, to_home, session_id, keep
    )
    cwd = _transcript_cwd(dest_file)

    if not keep:
        session_file.unlink()

    return MoveResult(
        source_file=session_file,
        dest_file=dest_file,
        sidecar_moved=False,
        session_id=session_id,
        cwd=cwd,
        kept_source=keep,
        agent="codex",
        index_entry_moved=index_moved,
    )


def resume_command(result: MoveResult, to_home: Path) -> str:
    """Build the shell command to resume the moved session.

    Args:
        result: Outcome of the move.
        to_home: Target config dir.

    Returns:
        A one-line shell command the user can paste.
    """
    import shlex

    to_home = to_home.expanduser().resolve()
    cd_part = f"cd {shlex.quote(result.cwd)} && " if result.cwd else ""
    home_quoted = shlex.quote(str(to_home))
    # Always name the config dir explicitly, even for the default
    # home: the pasted command then works regardless of any
    # CLAUDE_CONFIG_DIR / CODEX_HOME lingering in the user's shell.
    if result.agent == "codex":
        return (
            f"{cd_part}CODEX_HOME={home_quoted} "
            f"codex resume {result.session_id}"
        )
    return (
        f"{cd_part}CLAUDE_CONFIG_DIR={home_quoted} "
        f"claude --resume {result.session_id}"
    )


def _resolve_agent(
    agent_arg: Optional[str],
    from_home_arg: Optional[str],
    to_home: Path,
) -> str:
    """Determine the agent kind for a move-account invocation.

    Explicit ``--agent`` wins; otherwise the kind is auto-detected
    from the structure of the target (and source, if given) homes:
    ``projects/`` means Claude, ``sessions/`` means Codex.

    Args:
        agent_arg: Explicit --agent value, if any.
        from_home_arg: Explicit --from value, if any.
        to_home: Target config dir.

    Returns:
        'claude' or 'codex'; exits with an error if undeterminable
        or if source and target disagree.
    """
    if agent_arg:
        return agent_arg.lower()
    to_kind = detect_home_kind(to_home)
    from_kind = (
        detect_home_kind(Path(from_home_arg)) if from_home_arg else None
    )
    if to_kind and from_kind and to_kind != from_kind:
        print(
            f"Error: source looks like a {from_kind} home but target "
            f"looks like a {to_kind} home",
            file=sys.stderr,
        )
        sys.exit(1)
    kind = to_kind or from_kind
    if not kind:
        print(
            f"Error: cannot tell whether {to_home} is a Claude or Codex "
            "config dir; pass --agent claude|codex",
            file=sys.stderr,
        )
        sys.exit(1)
    return kind


def candidate_source_homes(agent: str, to_home: Path) -> List[Path]:
    """Discover local config dirs that might hold the session.

    Candidates, in order: the active home (CLAUDE_CONFIG_DIR /
    CODEX_HOME env var), the default home (~/.claude or ~/.codex),
    and sibling dirs matching ``~/.claude*`` / ``~/.codex*`` (e.g.
    ``~/.claude-rja``). Dirs lacking the expected subdir
    (``projects/`` or ``sessions/``) and the target home itself are
    excluded.

    Args:
        agent: 'claude' or 'codex'.
        to_home: Target config dir (never a source candidate).

    Returns:
        Deduplicated, existing source-home candidates.
    """
    if agent == "codex":
        env_var, prefix, required = "CODEX_HOME", ".codex", "sessions"
    else:
        env_var, prefix, required = (
            "CLAUDE_CONFIG_DIR", ".claude", "projects"
        )

    raw: List[Path] = []
    env_val = os.environ.get(env_var)
    if env_val:
        raw.append(Path(env_val).expanduser())
    raw.append(Path.home() / prefix)
    raw.extend(sorted(Path.home().glob(prefix + "*")))

    to_resolved = to_home.expanduser().resolve()
    seen = set()
    out: List[Path] = []
    for cand in raw:
        cand = cand.expanduser()
        if not (cand / required).is_dir():
            continue
        resolved = cand.resolve()
        if resolved == to_resolved or resolved in seen:
            continue
        seen.add(resolved)
        out.append(cand)
    return out


def run_move_account(
    session: str,
    to_home_arg: str,
    from_home_arg: Optional[str],
    keep: bool,
    agent: Optional[str] = None,
) -> None:
    """CLI driver for ``aichat move-account``.

    Args:
        session: Session UUID (full/partial) or session name.
        to_home_arg: Target config dir (Claude or Codex home).
        from_home_arg: Source config dir; when omitted, all local
            homes of the agent's kind (env-var home, default home,
            and ``~/.claude*`` / ``~/.codex*`` siblings) are searched
            for the session.
        keep: Copy instead of move.
        agent: 'claude' or 'codex'; auto-detected from the home dirs
            when omitted.
    """
    from claude_code_tools.session_utils import (
        get_claude_home,
        get_codex_home,
    )

    to_home = Path(to_home_arg).expanduser()
    agent = _resolve_agent(agent, from_home_arg, to_home)

    if agent == "codex":
        finder = find_codex_sessions_in_home
        required_subdir = "sessions"
    else:
        finder = find_sessions_in_home
        required_subdir = "projects"

    if not (to_home / required_subdir).is_dir():
        print(
            f"Error: {to_home} does not look like a {agent} config dir "
            f"(no {required_subdir}/ subdir)",
            file=sys.stderr,
        )
        sys.exit(1)

    if from_home_arg:
        from_home = (
            get_codex_home(from_home_arg)
            if agent == "codex"
            else get_claude_home(from_home_arg)
        )
        if from_home.expanduser().resolve() == to_home.resolve():
            print(
                "Error: source and target account dirs are the same: "
                f"{from_home}",
                file=sys.stderr,
            )
            sys.exit(1)
        candidates = finder(from_home, session)
        if not candidates:
            print(
                f"Error: no session matching '{session}' in {from_home}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        homes = candidate_source_homes(agent, to_home)
        if not homes:
            print(
                f"Error: no local {agent} config dirs found to search; "
                "pass --from",
                file=sys.stderr,
            )
            sys.exit(1)
        matches = [
            (home, cands)
            for home in homes
            if (cands := finder(home, session))
        ]
        if not matches:
            searched = ", ".join(str(h) for h in homes)
            print(
                f"Error: no session matching '{session}' in any of: "
                f"{searched}",
                file=sys.stderr,
            )
            sys.exit(1)
        if len(matches) > 1:
            print(
                f"Error: '{session}' matches sessions in multiple "
                "accounts:",
                file=sys.stderr,
            )
            for home, cands in matches:
                for cand in cands:
                    label = f" ({cand.title})" if cand.title else ""
                    print(
                        f"  {home}: {cand.session_id}{label}",
                        file=sys.stderr,
                    )
            print("Disambiguate with --from.", file=sys.stderr)
            sys.exit(1)
        from_home, candidates = matches[0]
    if len(candidates) > 1:
        print(
            f"Error: multiple sessions match '{session}':", file=sys.stderr
        )
        for cand in candidates:
            label = f" ({cand.title})" if cand.title else ""
            print(f"  {cand.session_id}{label}", file=sys.stderr)
        print("Use a more specific ID or name.", file=sys.stderr)
        sys.exit(1)

    cand = candidates[0]
    label = f" ({cand.title})" if cand.title else ""
    verb = "Copying" if keep else "Moving"
    print(f"{verb} {agent} session {cand.session_id}{label}")
    print(f"  from account: {from_home}")
    print(f"  to account:   {to_home}")

    mover = (
        move_codex_session_between_homes
        if agent == "codex"
        else move_session_between_homes
    )
    try:
        result = mover(cand.path, from_home, to_home, keep=keep)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  transcript:   {result.dest_file}")
    if result.sidecar_moved:
        print(f"  sidecar dir:  {result.dest_file.with_suffix('')}")
    if result.index_entry_moved:
        print("  thread name:  moved in session_index.jsonl")
    if result.kept_source:
        print("  source left in place (--keep)")
    print("\nResume with:")
    print(f"  {resume_command(result, to_home)}")
