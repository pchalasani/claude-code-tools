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
import subprocess
from pathlib import Path
from typing import Optional

from .backends import Answer, BackendError, _BaseBackend
from .codex_session import find_codex_session_file, fork_codex_session
from .config import TunnelConfig
from .store import ThreadRecord, TunnelStore

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
        if access == "bash":
            flags += ["-c", "sandbox_workspace_write.network_access=true"]
    if codex.model:
        flags += ["-m", codex.model]
    return flags


class CodexHeadlessBackend(_BaseBackend):
    """`codex exec resume` per question; prompt on stdin, JSONL on stdout."""

    name = "headless"
    agent = "codex"

    def __init__(self, cfg: TunnelConfig, store: TunnelStore) -> None:
        """Keep config/store and per-thread access-change notes."""
        super().__init__(cfg, store)
        # Thread keys whose access level changed since their last turn: the
        # next turn re-sends the outbox instruction (it normally rides only
        # on the fork-creation prompt, persisting in the fork's context).
        self._access_changed: set[str] = set()

    def _on_access_changed(self, rec: ThreadRecord, old: str, new: str) -> None:
        """Mark the thread so its next prompt carries updated instructions."""
        self._access_changed.add(rec.thread_key)

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

    def _turn_preamble(
        self, rec: ThreadRecord, fork: bool, extra_system: str
    ) -> str:
        """Instructions prepended to this turn's prompt.

        Codex has no --append-system-prompt, so the persona rides on the
        fork-creation prompt (persisting in the fork's context from then
        on). The outbox instruction (extra_system) is included on the fork
        turn and re-sent once after a live access change.
        """
        parts: list[str] = []
        if fork:
            persona = self.cfg.codex.persona.replace(
                "{platform}", self.cfg.platform
            )
            if persona:
                parts.append(persona)
        if extra_system and (fork or rec.thread_key in self._access_changed):
            parts.append(extra_system)
        self._access_changed.discard(rec.thread_key)
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
        else:
            fork_id = rec.fork_session_id

        preamble = self._turn_preamble(rec, fork, extra_system)
        prompt = f"{preamble}\n\n---\n\n{question}" if preamble else question

        # "-" = read the prompt from stdin: no shell quoting, and a question
        # that begins with "-" can't be mistaken for a CLI flag.
        argv = [
            self.cfg.codex.binary,
            "exec",
            "resume",
            fork_id,
            "-",
            *build_codex_flags(self.cfg, rec.access),
            *self.cfg.codex.headless_extra_args,
        ]
        try:
            result = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
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

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip()[-500:]
            raise BackendError(f"codex exited {result.returncode}: {tail}")

        text, errors = _parse_exec_events(result.stdout)
        if not text:
            detail = "; ".join(errors) or (result.stderr or "").strip()[-300:]
            raise BackendError(
                f"Empty answer from codex{': ' + detail if detail else ''}"
            )

        rec.fork_session_id = fork_id
        self.store.upsert(rec)
        return Answer(
            text=text.strip(),
            fork_session_id=fork_id,
            new_thread=fork,
            attachments=self._end_turn(outbox, snapshot),
        )


def _parse_exec_events(stdout: str) -> tuple[str, list[str]]:
    """(final answer, errors) from a `codex exec --json` event stream.

    The answer is the LAST agent_message item — the same message
    --output-last-message would write. Error items are collected for
    diagnostics but are not fatal by themselves (codex emits benign
    warnings, e.g. skill-budget notes, as error items).
    """
    answer = ""
    errors: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type", "")
        if etype == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                answer = str(item["text"])
            elif item.get("type") == "error":
                errors.append(str(item.get("message", ""))[:200])
        elif etype in ("turn.failed", "error"):
            err = event.get("error")
            message = (
                err.get("message", "") if isinstance(err, dict) else ""
            ) or event.get("message", "")
            if message:
                errors.append(str(message)[:200])
    return answer, errors
