"""In-place trimming of a Claude Code session JSONL file.

Unlike ``trim_and_create_session`` (which mints a new session file with a
new UUID and lineage metadata), this module rewrites the ORIGINAL session
file in place so the session keeps its identity: the next time the same
session is resumed, Claude Code loads the trimmed transcript.

Safety model:

- Symlinked session paths are resolved up front: every read, backup and
  the final swap operate on the REAL file, so a symlink is never
  replaced by a regular file (which would split the session identity).
- The trimmed content is written to a temp file in the same directory and
  atomically swapped in with ``os.replace``. Claude Code appends to the
  transcript by path (it holds no persistent fd), so a swap between
  prompts is safe.
- Before the swap - and only on a real apply - the original file is
  snapshotted to a timestamped backup name
  (``<id>.pre-trim-<ts>.jsonl.bak``) with ``shutil.copy2``: the backup
  is an INDEPENDENT copy of the original, and truncation placeholders
  reference it ("See line N of <backup> for full content"). The
  ``.bak`` suffix keeps backups out of ``*.jsonl`` session listings.
  Backup paths are reserved atomically (``O_CREAT | O_EXCL``), so
  concurrent trims can never pick the same backup name and overwrite
  each other's backups.
- A stat guard (inode, size, mtime) detects changes that land while
  the trim is being computed - including during the backup copy, so a
  torn snapshot is always discarded - and retries. In addition, an
  open fd on the original inode is held through the swap: a line
  appended in the window around ``os.replace`` lands on the OLD inode
  and is SPLICED into the swapped-in file right after the trimmed
  content - BEFORE any newer lines already appended by path to the new
  file, so the uuid/parentUuid order is preserved - and mirrored into
  the backup. The old inode is polled until it stays quiet for a short
  grace period, catching writers that opened the file pre-swap and
  finish shortly after the swap.
- Writers that append BY PATH (what Claude Code does for every
  message) are never lost: such a line lands before the swap (the stat
  guard retries), or on the swapped-in file, or - if it races a merge
  splice - is salvaged off the replaced inode and re-appended. Only a
  hypothetical writer holding a PRE-SWAP fd that appends after the
  grace period degrades to best-effort: its bytes land on the unlinked
  pre-trim inode (the backup is a plain copy by contract, not that
  inode) and are not folded into the live session.
"""

import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from claude_code_tools.trim_session import (
    MIN_TOKEN_SAVINGS,
    create_placeholder,
    detect_agent_strict,
)
from claude_code_tools.trim_session_claude import (
    build_tool_name_mapping,
    process_claude_session,
)

# Give up after this many attempts if the session file keeps changing
# under us (e.g. Claude Code appending mid-trim).
MAX_ATTEMPTS = 3

# After the swap, the old inode is polled until it has been quiet for
# this long; a straggler holding a pre-swap handle usually completes
# its in-flight write well within this window.
_MERGE_QUIET_SECS = 0.25
# Poll interval while waiting out the quiet window.
_MERGE_POLL_SECS = 0.02
# Hard cap on total post-swap merge time so a pathological writer that
# keeps appending to the dead inode cannot stall the trim forever.
_MERGE_MAX_SECS = 5.0


def _sig_of_stat(st: os.stat_result) -> Tuple[int, int, int]:
    """Return the (inode, size, mtime_ns) change-detection signature."""
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _stat_signature(path: Path) -> Tuple[int, int, int]:
    """Return the change-detection signature for ``path``."""
    return _sig_of_stat(os.stat(path))


def _backup_base(session_file: Path) -> str:
    """Timestamped basename stem shared by all backup candidates."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{session_file.stem}.pre-trim-{ts}"


def _backup_candidate(session_file: Path, base: str, counter: int) -> Path:
    """The ``counter``-th candidate backup path for ``base``.

    Example: ``05baf129-...jsonl`` ->
    ``05baf129-....pre-trim-20260704-083012.jsonl.bak`` (counter 1),
    ``...-083012-2.jsonl.bak`` (counter 2), ...
    """
    suffix = "" if counter == 1 else f"-{counter}"
    return session_file.with_name(f"{base}{suffix}.jsonl.bak")


def _predict_backup_path(session_file: Path) -> Path:
    """Backup path a real apply would likely use (nothing is created).

    Only used for dry runs, where the name merely appears in the
    placeholder text of the discarded preview output.
    """
    base = _backup_base(session_file)
    counter = 1
    while True:
        candidate = _backup_candidate(session_file, base, counter)
        if not candidate.exists():
            return candidate
        counter += 1


def _reserve_backup_path(session_file: Path) -> Path:
    """Atomically create (reserve) a unique timestamped backup path.

    ``O_CREAT | O_EXCL`` guarantees the returned path was created by
    this very call: a concurrent trim (or any other process) that races
    for the same timestamped name gets ``FileExistsError`` and moves on
    to the next counter, so an existing backup is never overwritten.
    """
    base = _backup_base(session_file)
    counter = 1
    while True:
        candidate = _backup_candidate(session_file, base, counter)
        try:
            fd = os.open(
                str(candidate),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            counter += 1
            continue
        os.close(fd)
        return candidate


def _backup_original(
    session_file: Path, backup_path: Path, expected_ino: int
) -> Optional[bool]:
    """Materialize the pre-trim backup at ``backup_path``.

    The backup is an INDEPENDENT snapshot taken with ``shutil.copy2``
    (the feature contract: a real apply keeps a timestamped copied
    backup - never a hard link to the live inode). An append racing
    the copy would leave a torn snapshot, but the caller re-checks the
    session's stat signature right after this returns and retries, so
    a torn or stale snapshot is always discarded.

    Args:
        session_file: The (resolved) session path being trimmed.
        backup_path: The exclusively reserved backup path.
        expected_ino: Inode the caller opened; if the path no longer
            points at it, the snapshot would be of some other file.

    Returns:
        True once the copy exists, or None if the inode at
        ``session_file`` changed and the caller must retry.
    """
    try:
        if os.stat(session_file).st_ino != expected_ino:
            return None
    except OSError:
        return None
    shutil.copy2(session_file, backup_path)
    return True


def _append_bytes(path: Path, data: bytes) -> None:
    """Append raw bytes to ``path`` via O_APPEND, retrying short writes.

    ``os.write`` may write fewer bytes than requested (a partial
    write), especially for very large buffers; a single unchecked
    call could silently drop the tail of a merged session line. The
    write is therefore retried from the returned offset until the
    whole buffer is on disk; a zero-byte write means no progress and
    raises instead of looping forever.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
    try:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise OSError(
                    f"os.write returned {written} appending to {path} "
                    f"({len(view) - offset} bytes unwritten)"
                )
            offset += written
    finally:
        os.close(fd)


def _read_range(fd: int, start: int, end: int) -> bytes:
    """Read bytes ``[start, end)`` from ``fd`` (exclusively owned)."""
    os.lseek(fd, start, os.SEEK_SET)
    chunks: List[bytes] = []
    remaining = end - start
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _drain_fd_growth(
    fd: int,
    drained_upto: int,
    sink: Callable[[bytes], None],
) -> int:
    """Poll ``fd`` until it stays quiet; feed newly grown bytes to
    ``sink``.

    Any bytes past ``drained_upto`` are read and handed to ``sink`` as
    they appear. The fd is polled until it has been quiet for
    ``_MERGE_QUIET_SECS`` (capped at ``_MERGE_MAX_SECS`` total).

    Args:
        fd: Readable descriptor on the inode to watch.
        drained_upto: Leading bytes of the inode already handled.
        sink: Callable receiving each newly appended chunk.

    Returns:
        The number of leading bytes of the inode handled in total.
    """
    deadline = time.monotonic() + _MERGE_MAX_SECS
    quiet_until = time.monotonic() + _MERGE_QUIET_SECS
    while True:
        current_size = os.fstat(fd).st_size
        if current_size > drained_upto:
            chunk = _read_range(fd, drained_upto, current_size)
            if chunk:
                sink(chunk)
                drained_upto += len(chunk)
                # Growth resets the quiet window (writer still active).
                quiet_until = time.monotonic() + _MERGE_QUIET_SECS
                continue
        now = time.monotonic()
        if now >= quiet_until or now >= deadline:
            return drained_upto
        time.sleep(_MERGE_POLL_SECS)


def _splice_into_file(
    session_file: Path, insert_at: int, data: bytes
) -> None:
    """Insert ``data`` into ``session_file`` at offset ``insert_at``.

    Bytes that raced the original swap onto the old inode belong
    BETWEEN the trimmed content and any lines appended by path to the
    swapped-in file (those were written later), so a plain append is
    only correct while nothing has landed past ``insert_at`` - and
    even then an append could sneak in underneath. The file is
    therefore rebuilt as head + data + tail and atomically swapped in,
    guarded by a stat signature: a by-path append racing the rebuild
    triggers a (bounded) retry on fresh bytes. After the swap, the
    replaced inode is drained - a write landing there in the
    guard-to-replace window would otherwise vanish with the unlinked
    inode; such bytes are the newest writes, so re-appending them
    keeps chronological order. If every rebuild attempt is beaten by a
    racing writer, ``data`` is plainly appended instead: bytes are
    never dropped, ordering degrades to best-effort under such
    pathological append rates.
    """
    for _attempt in range(MAX_ATTEMPTS):
        live_fd = os.open(str(session_file), os.O_RDONLY)
        try:
            live_st = os.fstat(live_fd)
            live_sig = _sig_of_stat(live_st)
            live_bytes = _read_range(live_fd, 0, live_st.st_size)
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=session_file.parent,
                prefix=f".{session_file.stem}.merge-tmp-",
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(tmp_fd, "wb") as tmp_file:
                    tmp_file.write(live_bytes[:insert_at])
                    tmp_file.write(data)
                    tmp_file.write(live_bytes[insert_at:])
                shutil.copymode(session_file, tmp_path)
                if _stat_signature(session_file) != live_sig:
                    # A writer appended mid-rebuild: retry on fresh
                    # bytes so its line is not clobbered by the swap.
                    tmp_path.unlink(missing_ok=True)
                    continue
                os.replace(tmp_path, session_file)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            # Salvage writes that hit the replaced (now unlinked)
            # inode between the signature check and the swap: they
            # are the newest writes, so appending preserves order.
            _drain_fd_growth(
                live_fd,
                live_st.st_size,
                lambda chunk: _append_bytes(session_file, chunk),
            )
            return
        finally:
            os.close(live_fd)
    _append_bytes(session_file, data)


def _merge_tail_appends(
    orig_fd: int,
    merged_upto: int,
    insert_at: int,
    session_file: Path,
    backup_path: Path,
) -> int:
    """Merge appends racing the swap of ``session_file``.

    ``orig_fd`` is an open descriptor on the pre-trim inode. Any bytes
    past ``merged_upto`` were appended by a concurrent writer (e.g.
    Claude Code) after the trimmed snapshot was validated. They are
    SPLICED into the swapped-in session file at ``insert_at`` - right
    after the trimmed content and BEFORE any lines appended by path to
    the new file after the swap (those are chronologically later), so
    the uuid/parentUuid chain keeps its order. Each merged chunk is
    also appended to the copied backup, which thereby stays a faithful
    superset of the original. The old inode is polled until it stays
    quiet for ``_MERGE_QUIET_SECS`` (capped at ``_MERGE_MAX_SECS``
    total), so a writer that opened the file before the swap but
    writes shortly after ``os.replace`` is still caught.

    Args:
        orig_fd: Open descriptor on the pre-trim inode.
        merged_upto: Bytes of the old inode already reflected in the
            trimmed content (its size when the trim was computed).
        insert_at: Offset in the live file where old-inode bytes
            belong (the trimmed content's size).
        session_file: The live (swapped-in) session path.
        backup_path: The copied backup mirroring merged bytes.

    Returns:
        The number of leading bytes of the old inode reflected in the
        swapped-in file (original size plus all merged tail bytes).
    """

    def merge_chunk(chunk: bytes) -> None:
        nonlocal insert_at
        _splice_into_file(session_file, insert_at, chunk)
        _append_bytes(backup_path, chunk)
        insert_at += len(chunk)

    return _drain_fd_growth(orig_fd, merged_upto, merge_chunk)


def _trim_once(
    session_file: Path,
    target_tools: Optional[Set[str]],
    threshold: int,
    trim_assistant_messages: Optional[int],
    dry_run: bool,
    min_token_savings: int,
) -> Optional[Dict[str, Any]]:
    """Run a single trim attempt against ``session_file``.

    Returns:
        The result dict on success (including dry-run and
        nothing-to-trim outcomes), or None if the session file changed
        while the trim was computed and the caller should retry.
    """
    # Hold a handle on the CURRENT inode for the whole attempt: an
    # append that races with the final swap lands on this inode and
    # stays readable through the fd even after os.replace() repoints
    # the path at the trimmed file.
    orig_fd = os.open(str(session_file), os.O_RDONLY)
    backup_path: Optional[Path] = None
    backup_reserved = False
    tmp_path: Optional[Path] = None
    replaced = False
    try:
        sig_before = _sig_of_stat(os.fstat(orig_fd))
        size_before = sig_before[1]

        if dry_run:
            backup_path = _predict_backup_path(session_file)
        else:
            backup_path = _reserve_backup_path(session_file)
            backup_reserved = True

        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=session_file.parent,
            prefix=f".{session_file.stem}.trim-tmp-",
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        tool_map = build_tool_name_mapping(session_file)
        (
            num_tools_trimmed,
            num_assistant_trimmed,
            chars_saved,
        ) = process_claude_session(
            session_file,
            tmp_path,
            tool_map,
            target_tools,
            threshold,
            create_placeholder,
            new_session_id=None,
            trim_assistant_messages=trim_assistant_messages,
            parent_file=str(backup_path),
        )
        tokens_saved = int(chars_saved / 4)
        size_after = tmp_path.stat().st_size

        result: Dict[str, Any] = {
            "applied": False,
            "dry_run": dry_run,
            "nothing_to_trim": tokens_saved < min_token_savings,
            "num_tools_trimmed": num_tools_trimmed,
            "num_assistant_trimmed": num_assistant_trimmed,
            "chars_saved": chars_saved,
            "tokens_saved": tokens_saved,
            "backup_file": None,
            "session_file": str(session_file),
            "size_before": size_before,
            "size_after": size_after,
        }

        if dry_run or result["nothing_to_trim"]:
            tmp_path.unlink(missing_ok=True)
            if backup_reserved:
                backup_path.unlink(missing_ok=True)
            return result

        # Snapshot the original to the reserved backup (copy2) so
        # placeholder references ("See line N of <backup>") point at
        # surviving content.
        backup_ok = _backup_original(
            session_file, backup_path, sig_before[0]
        )
        if backup_ok is None:
            tmp_path.unlink(missing_ok=True)
            backup_path.unlink(missing_ok=True)
            return None

        # Retry if the session changed while we trimmed: the trimmed
        # temp file was computed from a stale snapshot.
        if _stat_signature(session_file) != sig_before:
            tmp_path.unlink(missing_ok=True)
            backup_path.unlink(missing_ok=True)
            return None

        shutil.copymode(session_file, tmp_path)
        os.replace(tmp_path, session_file)
        replaced = True

        # Splice lines appended to the OLD inode after the stat check
        # above (i.e. racing the swap) into the new file - right
        # after the trimmed content, before any newer by-path appends
        # - polling until the old inode stays quiet for a grace
        # period. Merged bytes are mirrored into the copied backup.
        _merge_tail_appends(
            orig_fd,
            size_before,
            size_after,
            session_file,
            backup_path,
        )
        # Report the ACTUAL on-disk size: old-inode bytes were spliced
        # in above, and Claude Code may already have appended by path
        # to the swapped-in file - bytes the pre-swap temp size (even
        # adjusted by the merged byte count) cannot account for.
        result["size_after"] = session_file.stat().st_size

        result["applied"] = True
        result["backup_file"] = str(backup_path)
        return result
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        if backup_reserved and backup_path is not None and not replaced:
            backup_path.unlink(missing_ok=True)
        raise
    finally:
        os.close(orig_fd)


def trim_session_in_place(
    session_file: Path,
    target_tools: Optional[Set[str]] = None,
    threshold: int = 500,
    trim_assistant_messages: Optional[int] = None,
    dry_run: bool = False,
    min_token_savings: int = MIN_TOKEN_SAVINGS,
) -> Dict[str, Any]:
    """Trim a Claude session file in place (or preview the savings).

    Applies the same deterministic trim as ``trim_and_create_session``
    (truncate long tool results and tool_use inputs, optionally replace
    long assistant messages, neutralize context-full markers) but keeps
    the file's identity: no new UUID, no sessionId rewrite, no
    trim_metadata/lineage injection. Line count and the uuid/parentUuid
    chain are preserved (content is replaced, lines are never deleted).

    A symlinked path is resolved first: the REAL file is trimmed (and
    backed up next to itself) and the symlink keeps pointing at it;
    the returned ``session_file`` is the resolved path.

    Args:
        session_file: Path to the Claude session JSONL file.
        target_tools: Lowercased tool names whose results to trim
            (None = all tools).
        threshold: Character threshold — content this long or longer is
            trimmed, keeping the first ``threshold`` characters. Must be
            a positive integer.
        trim_assistant_messages: Positive N trims the first N
            over-threshold assistant messages; negative N trims all
            except the last ``abs(N)``; None skips assistant trimming.
        dry_run: If True, compute savings without touching the file
            (no backup is created either).
        min_token_savings: Minimum estimated tokens saved for the trim
            to be worthwhile; below this nothing is written.

    Returns:
        Dict with keys: applied, dry_run, nothing_to_trim,
        num_tools_trimmed, num_assistant_trimmed, chars_saved,
        tokens_saved, backup_file (None unless applied), session_file,
        size_before, size_after.

    Raises:
        FileNotFoundError: If ``session_file`` does not exist.
        ValueError: If the file is not positively identified as a
            Claude session (Codex files, empty files, random text and
            marker-less JSONL are all rejected untouched), or
            ``threshold`` is not a positive integer.
        RuntimeError: If the file keeps changing during the trim.
    """
    session_file = Path(session_file)
    if threshold is None or threshold < 1:
        raise ValueError(
            f"threshold must be a positive integer (got {threshold})"
        )
    if not session_file.is_file():
        raise FileNotFoundError(f"Session file not found: {session_file}")
    # Operate on the real file: with a symlinked path, the final
    # os.replace would otherwise overwrite the SYMLINK with a regular
    # file, leaving the real transcript untrimmed and splitting the
    # session identity.
    session_file = session_file.resolve()

    agent = detect_agent_strict(session_file)
    if agent != "claude":
        raise ValueError(
            f"In-place trim only supports Claude sessions "
            f"(detected: {agent or 'unknown'})"
        )

    for _attempt in range(MAX_ATTEMPTS):
        result = _trim_once(
            session_file,
            target_tools,
            threshold,
            trim_assistant_messages,
            dry_run,
            min_token_savings,
        )
        if result is not None:
            return result

    raise RuntimeError(
        f"Could not trim {session_file}: session file changed during "
        f"trim ({MAX_ATTEMPTS} attempts)"
    )
