"""Codex execution backend: answer questions against a file-level fork.

The codex analog of HeadlessBackend. Every turn runs
``codex exec resume <fork-id> --json`` with the prompt on stdin:

- first turn: fork the expert session AT THE FILE LEVEL (codex's ``exec
  resume`` appends to the resumed session's own rollout, and its native
  ``fork`` command is interactive-only), then resume the copy;
- later turns: resume the same fork id — stable across turns, unlike
  claude's per-turn fork ids.

Access levels map onto codex's OS sandbox rather than tool lists (codex has
none): read -> read-only, write -> workspace-write (which can also run
sandboxed commands), bash -> workspace-write + network, all ->
--dangerously-bypass-approvals-and-sandbox (double opt-in gated). The
``codex exec resume`` subcommand has no ``--sandbox`` flag, so the sandbox
is set via ``-c`` config overrides (validated against codex-cli 0.144).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from .backends import Answer, BackendError, _BaseBackend
from .codex_session import find_codex_session_file, fork_codex_session
from .config import TunnelConfig
from .store import ThreadRecord

# Auth env var that overrides the ChatGPT login; stripped from fork envs so
# forks run under the session owner's subscription (codex.unset_api_key).
CODEX_AUTH_OVERRIDE_VARS = ("OPENAI_API_KEY",)

# Access level -> codex sandbox_mode (the "all" level bypasses the sandbox
# entirely and is handled separately).
_SANDBOX_MODES = {
    "read": "read-only",
    "write": "workspace-write",
    "bash": "workspace-write",
}

# Codex tool "features" (from `codex features list`, codex-cli 0.144) that
# take external ACTIONS outside the OS shell sandbox — browser/computer use,
# apps/connectors, image generation, host code mode, and the owner's config
# hooks. The OS sandbox does not govern these, so a non-"all" handle disables
# them (via `--disable`) for parity with claude read's tool allowlist. This
# list is version-specific: codex may add new action features, so publishers
# sharing codex sessions with less-trusted colleagues should audit
# `codex features list` (see docs "Codex CLI sessions" / Security model).
_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "code_mode_host",
    "hooks",
)

# Bare flags in [codex] headless_extra_args that must never reach codex:
# sandbox/approval bypasses (would escalate a read handle past its access
# level — that is the >share gate's job alone) and persistence changers
# (--ephemeral stops the fork rollout from being appended to, breaking the
# stable-fork contract). Sandbox enforcement is authoritative because the
# built flags are appended last, but these are stripped defensively.
_STRIPPED_BARE_FLAGS = frozenset(
    {
        "--dangerously-bypass-approvals-and-sandbox",
        "--full-auto",
        "--yolo",
        "--ephemeral",
        # The option terminator: everything after "--" is positional, so a
        # "--" here would turn the enforced sandbox flags (appended last)
        # into positionals and silently disable them. `codex exec resume`
        # takes the prompt via stdin ("-"), so "--" has no legitimate use.
        "--",
    }
)
# Flags that take a following value; both tokens are dropped. -o/
# --output-last-message write an owner-chosen file from codex's HOST process
# (outside the model sandbox), so a read handle must not carry it.
_STRIPPED_VALUE_FLAGS = frozenset(
    {"-s", "--sandbox", "-o", "--output-last-message"}
)
# `-c key=value` keys that override sandbox/approval confinement.
_SANDBOX_CFG_KEYS = (
    "sandbox_mode",
    "approval_policy",
    "sandbox_workspace_write",
)


def _targets_sandbox(value: str) -> bool:
    """True if a ``-c`` config value overrides a sandbox/approval setting."""
    return any(value.lstrip().startswith(k) for k in _SANDBOX_CFG_KEYS)


def _config_payload(arg: str, nxt: str) -> Optional[str]:
    """The ``key=value`` a config token carries, or None if it isn't one.

    Handles every codex form: ``-c key=val`` / ``--config key=val`` (two
    tokens, payload = ``nxt``), ``--config=key=val`` / ``-c=key=val``, and
    the combined ``-ckey=val``.
    """
    if arg in ("-c", "--config"):
        return nxt
    if arg.startswith("--config="):
        return arg[len("--config="):]
    if arg.startswith("-c"):
        return arg[2:].lstrip("=")
    return None


def _sanitize_extra_args(extra: list[str]) -> list[str]:
    """Strip security-sensitive tokens from owner-configured extra args.

    ``headless_extra_args`` applies to every turn of every handle, so a
    sandbox/approval override (or ``--ephemeral``) there would bypass
    per-handle access control or the stable-fork contract for even a read
    handle. Anything targeting those is removed; ordinary overrides
    (``-c model=…``, ``-m``, ``--json``) are kept untouched.
    """
    out: list[str] = []
    i = 0
    n = len(extra)
    while i < n:
        arg = extra[i]
        if arg in _STRIPPED_BARE_FLAGS:
            i += 1
            continue
        if arg in _STRIPPED_VALUE_FLAGS:  # -s / --sandbox <value>
            i += 2
            continue
        # Attached/equals forms: -s=mode, -smode, --sandbox=mode, -ofile,
        # -o=file, --output-last-message=file. A short "-s…"/"-o…" token is
        # always the sandbox/output flag ("--…" long options don't match
        # these short prefixes).
        if (
            arg.startswith("-s")
            or arg.startswith("-o")
            or arg.startswith("--sandbox=")
            or arg.startswith("--output-last-message=")
        ):
            i += 1
            continue
        payload = _config_payload(arg, extra[i + 1] if i + 1 < n else "")
        if payload is not None:
            two_token = arg in ("-c", "--config")
            if _targets_sandbox(payload):
                i += 2 if two_token else 1
                continue
            # Benign config override: keep the token(s) verbatim.
            out.append(arg)
            if two_token and i + 1 < n:
                out.append(extra[i + 1])
            i += 2 if two_token else 1
            continue
        out.append(arg)
        i += 1
    return out


def build_codex_flags(cfg: TunnelConfig, access: str = "read") -> list[str]:
    """Common codex CLI flags for a fork turn (`codex exec resume` accepts
    no --sandbox/--cd, so sandboxing goes through -c overrides).

    Args:
        cfg: Tunnel configuration ([codex] table).
        access: Per-handle access level ("read"/"write"/"bash"/"all").

    Returns:
        Argument list (excluding the binary, subcommand, session id, and
        prompt).
    """
    codex = cfg.codex
    flags = ["--json", "--skip-git-repo-check"]
    if access == "all" and codex.allow_skip_permissions:
        # Full access: no sandbox, no approvals — the codex analog of
        # claude's --dangerously-skip-permissions. Never reached ungated:
        # _require_binding refuses "all" when the gate is off.
        flags.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        mode = _SANDBOX_MODES.get(access, "read-only")
        flags += ["-c", f'sandbox_mode="{mode}"']
        # Non-interactive turns can't answer approval prompts.
        flags += ["-c", 'approval_policy="never"']
        if mode == "workspace-write":
            # Pin network EXPLICITLY per level so a write handle can't
            # inherit network_access=true from the owner's config.toml
            # (which would collapse the write/bash distinction). bash grants
            # network; write denies it. read-only has no workspace network.
            net = "true" if access == "bash" else "false"
            flags += ["-c", f"sandbox_workspace_write.network_access={net}"]
        # The OS sandbox governs only shell commands. MCP servers, web search,
        # and codex's default-on action features (browser/computer use, apps,
        # image gen, host code mode, config hooks) run OUTSIDE it and could
        # write/network regardless, so disable them for every non-"all" handle
        # — parity with claude read's tool allowlist. "all" (fully gated)
        # keeps them.
        flags += ["-c", "mcp_servers={}"]
        flags += ["-c", "tools.web_search=false"]
        for feature in _DISABLED_FEATURES:
            flags += ["--disable", feature]
    if codex.model:
        flags += ["-m", codex.model]
    return flags


class CodexHeadlessBackend(_BaseBackend):
    """`codex exec resume` per question; prompt on stdin, JSONL on stdout."""

    name = "headless"
    agent = "codex"

    def _on_access_changed(self, rec: ThreadRecord, old: str, new: str) -> None:
        """Persist a marker so the next prompt re-sends the outbox note.

        Durable on the ThreadRecord (not backend memory): the daemon builds
        a FRESH backend per turn and may restart between a failed turn and
        its retry, so an in-memory marker would silently drop the
        instructions. "intro" already implies the outbox note, so it is
        never downgraded.
        """
        if rec.pending_instructions != "intro":
            rec.pending_instructions = "outbox"
            self.store.upsert(rec)

    def _env(self, rec: ThreadRecord) -> dict[str, str]:
        """Subprocess env: CODEX_HOME pinned to the session's home, and (by
        default) API-key auth stripped so the fork uses the owner's ChatGPT
        login rather than an API key."""
        env = dict(os.environ)
        if rec.config_dir:
            env["CODEX_HOME"] = rec.config_dir
        if self.cfg.codex.unset_api_key:
            for var in CODEX_AUTH_OVERRIDE_VARS:
                env.pop(var, None)
        return env

    def _codex_home(self, rec: ThreadRecord) -> Optional[Path]:
        """The CODEX_HOME the bound session lives under (None = default)."""
        return Path(rec.config_dir) if rec.config_dir else None

    def _turn_preamble(self, rec: ThreadRecord, extra_system: str) -> str:
        """Instructions prepended to this turn's prompt.

        Codex has no --append-system-prompt, so the persona rides on the
        fork's first successful prompt (persisting in the fork's context
        from then on). The outbox instruction (extra_system) is included on
        that intro turn and re-sent once after a live access change. The
        record's ``pending_instructions`` marker is cleared only after a
        successful turn — never here — so a failed turn keeps it and the
        retry re-sends the instructions (across backend instances and
        daemon restarts alike).
        """
        pending = rec.pending_instructions
        parts: list[str] = []
        if pending == "intro":
            persona = self.cfg.codex.persona.replace(
                "{platform}", self.cfg.platform
            )
            if persona:
                parts.append(persona)
        if extra_system and pending in ("intro", "outbox"):
            parts.append(extra_system)
        return "\n\n".join(parts)

    def ask(self, thread_key: str, question: str) -> Answer:
        """Run one headless codex turn in the thread's fork (creating the
        file-level fork on the first turn) and return the result text."""
        rec = self._require_binding(thread_key)
        fork = not rec.fork_session_id
        add_dirs, extra_system, outbox, snapshot = self._begin_turn(rec)
        del add_dirs  # codex sandbox reads any path; no --add-dir needed

        if fork:
            expert_file = find_codex_session_file(
                rec.expert_session_id, self._codex_home(rec)
            )
            if expert_file is None:
                raise BackendError(
                    f"Codex session {rec.expert_session_id} not found under "
                    f"{rec.config_dir or '~/.codex'}/sessions."
                )
            try:
                fork_id, _ = fork_codex_session(expert_file)
            except (ValueError, OSError) as exc:
                raise BackendError(
                    f"Could not fork codex session: {exc}"
                ) from exc
            # Persist the fork id AND the pending-intro marker BEFORE the
            # turn runs: if codex times out or the parse fails, the retry —
            # possibly on a fresh backend or after a daemon restart —
            # resumes THIS fork (no duplicate copy, no lost turn), still
            # owes it the persona intro, and known_fork_ids() already
            # excludes it from `share`/`ask` auto-discovery (the copy is
            # the newest rollout in the project, so an unrecorded one
            # would win it).
            rec.fork_session_id = fork_id
            rec.pending_instructions = "intro"
            self.store.upsert(rec)
        else:
            fork_id = rec.fork_session_id

        preamble = self._turn_preamble(rec, extra_system)
        prompt = f"{preamble}\n\n---\n\n{question}" if preamble else question

        # "-" = read the prompt from stdin: no shell quoting, and a question
        # that begins with "-" can't be mistaken for a CLI flag. Sanitized
        # extra args come FIRST, the per-handle sandbox/approval flags LAST,
        # so a stray override in config can never weaken the enforced access
        # level (codex `-c` is last-wins).
        argv = [
            self.cfg.codex.binary,
            "exec",
            "resume",
            fork_id,
            "-",
            *_sanitize_extra_args(self.cfg.codex.headless_extra_args),
            *build_codex_flags(self.cfg, rec.access),
        ]
        try:
            returncode, stdout, stderr = _run_in_group(
                argv,
                prompt,
                cwd=rec.project_dir,
                env=self._env(rec),
                timeout=self.cfg.limits.answer_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"Timed out after {self.cfg.limits.answer_timeout_s:.0f}s"
            ) from exc
        except OSError as exc:
            raise BackendError(f"Could not run codex: {exc}") from exc

        if returncode != 0:
            tail = (stderr or stdout or "").strip()[-500:]
            raise BackendError(f"codex exited {returncode}: {tail}")

        text, errors, failed = _parse_exec_events(stdout)
        answer_text = text.strip()
        # A top-level turn.failed/error is fatal even if an agent_message was
        # emitted before it, and a whitespace-only message is not an answer:
        # either would otherwise report a bad turn as success and wrongly
        # clear the pending-intro marker. Strip BEFORE the emptiness check.
        # The retry then re-runs the same fork cleanly.
        if failed or not answer_text:
            detail = "; ".join(errors) or (stderr or "").strip()[-300:]
            reason = "codex turn failed" if failed else "Empty answer from codex"
            raise BackendError(f"{reason}{': ' + detail if detail else ''}")

        # The instructions reached the fork's context; stop re-sending.
        rec.pending_instructions = ""
        rec.fork_session_id = fork_id
        self.store.upsert(rec)
        return Answer(
            text=answer_text,
            fork_session_id=fork_id,
            new_thread=fork,
            attachments=self._end_turn(outbox, snapshot),
        )


def _run_in_group(
    argv: list[str],
    input_text: str,
    cwd: Optional[str],
    env: dict[str, str],
    timeout: float,
) -> tuple[int, str, str]:
    """Run codex in its OWN process group and kill the WHOLE group on timeout.

    The installed ``codex`` is a Node wrapper that spawns the native binary;
    ``subprocess.run``'s timeout would kill only the wrapper, leaving the
    native child alive to keep appending to the fork rollout and race the
    retry. ``start_new_session=True`` puts the wrapper + its children in a
    fresh group so a single ``killpg`` reaps them all (SIGTERM, brief grace,
    then SIGKILL).

    Returns (returncode, stdout, stderr). Raises TimeoutExpired (after the
    group is killed) or OSError, matching subprocess.run's contract.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        # Drain + reap only AFTER the whole group is dead, so a descendant
        # can't hold the pipes open and hang this. Bounded, then give up.
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.communicate(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise
    return proc.returncode, out, err


def _kill_group(proc: "subprocess.Popen") -> None:  # type: ignore[type-arg]
    """Terminate codex's WHOLE process group, guaranteed.

    SIGTERM the group, poll the GROUP (not just the wrapper) through a grace
    period, then SIGKILL the group UNCONDITIONALLY — a native child that
    ignores SIGTERM must not survive just because the wrapper already exited.
    ``killpg(pgid, 0)`` probes group membership; ESRCH means it is empty.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        try:  # no group / already reaped — fall back to the direct child
            proc.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(50):  # up to ~5s grace, exit early once the group empties
        time.sleep(0.1)
        try:
            os.killpg(pgid, 0)
        except OSError:
            break
    try:
        os.killpg(pgid, signal.SIGKILL)  # ESRCH (already gone) is harmless
    except OSError:
        pass


def _parse_exec_events(stdout: str) -> tuple[str, list[str], bool]:
    """(final answer, errors, failed) from a `codex exec --json` stream.

    The answer is the LAST agent_message item — the same message
    --output-last-message would write; only a genuine STRING ``text`` is
    accepted (a numeric/null value is not an answer). ``failed`` is True if
    any top-level ``turn.failed``/``error`` event appeared: that is a fatal
    turn outcome the caller must not paper over with an earlier partial
    message. Per-ITEM error entries (codex emits benign warnings, e.g.
    skill-budget notes, as ``item.completed`` errors) are diagnostics only,
    never fatal.
    """
    answer = ""
    errors: list[str] = []
    failed = False
    # Split only on "\n" (the JSONL delimiter): str.splitlines() would also
    # break on U+2028/U+2029/U+0085, shattering an agent_message whose text
    # legitimately contains one of them into invalid fragments.
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # `codex exec --json` emits pure JSONL; a non-empty unparseable
            # line means a truncated/corrupt stream. Treat it as a failed
            # turn so a valid earlier agent_message can't be returned as a
            # successful answer over a torn tail.
            failed = True
            errors.append(f"unparseable event: {line[:80]}")
            continue
        # Any line may decode to null/list/scalar; only object events count.
        if not isinstance(event, dict):
            continue
        etype = event.get("type", "")
        if etype == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") == "agent_message":
                # The LAST agent_message is authoritative, so replace the
                # candidate UNCONDITIONALLY: a final message whose text is
                # absent/null/non-string/empty clears the answer (→ ask()'s
                # empty-answer failure) instead of returning a stale earlier
                # message as if it were the turn's result.
                text = item.get("text")
                answer = text if isinstance(text, str) else ""
            elif item.get("type") == "error":
                errors.append(str(item.get("message", ""))[:200])
        elif etype in ("turn.failed", "error"):
            failed = True
            err = event.get("error")
            message = (
                err.get("message", "") if isinstance(err, dict) else ""
            ) or event.get("message", "")
            if message:
                errors.append(str(message)[:200])
    return answer, errors, failed
