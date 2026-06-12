"""Execution backends: answer a question against a forked Claude session.

Each external conversation thread is *bound* in the store to an expert
session (handle -> session_id + project dir) before any question runs, so a
backend reads everything it needs from the thread's ThreadRecord:

- first turn (empty `fork_session_id`): fork the expert session
  (`--resume <expert> --fork-session`) in its project dir;
- later turns: resume the thread's own fork (`--resume <fork>`).

Two interchangeable strategies (see docs/agent-tunnel-spec.md):

- HeadlessBackend: `claude -p` per question (clean JSON I/O; Agent SDK
  metering on subscription plans from 2026-06-15).
- TmuxBackend: a real interactive `claude` per thread in a window of a
  dedicated tmux session; question pasted in, answer read from the fork's
  JSONL transcript (interactive subscription metering).

Both apply the same hard read-only tool restrictions per turn.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .config import TunnelConfig
from .session import (
    extract_answer,
    list_session_files,
    make_marker,
    wait_for_new_session_file,
)
from .store import ThreadRecord, TunnelStore
from .tmux import TmuxSession


class BackendError(RuntimeError):
    """Answering a question failed; message is user-presentable."""


@dataclass
class Answer:
    """A completed answer from a fork."""

    text: str
    fork_session_id: str
    new_thread: bool


class Backend(Protocol):
    """Common interface for execution backends."""

    def ask(self, thread_key: str, question: str) -> Answer:
        """Answer `question` in the (already-bound) thread's fork."""
        ...

    def forget(self, thread_key: str) -> None:
        """Drop a thread mapping and any live resources."""
        ...

    def reap_idle(self) -> int:
        """Release idle resources; returns how many were released."""
        ...


def build_claude_flags(
    cfg: TunnelConfig, resume_id: str, fork: bool
) -> list[str]:
    """Common claude CLI flags for a fork invocation/launch.

    Args:
        cfg: Tunnel configuration (tool restrictions, model, persona).
        resume_id: Session id to resume (expert id when forking, else the
            fork's own id).
        fork: Whether to create a new fork of `resume_id`.

    Returns:
        Argument list (excluding the binary, -p, and the prompt).
    """
    claude = cfg.claude
    flags = ["--resume", resume_id]
    if fork:
        flags.append("--fork-session")
    if claude.allowed_tools:
        flags += ["--allowedTools", ",".join(claude.allowed_tools)]
    if claude.disallowed_tools:
        flags += ["--disallowedTools", ",".join(claude.disallowed_tools)]
    if claude.permission_mode:
        flags += ["--permission-mode", claude.permission_mode]
    if claude.model:
        flags += ["--model", claude.model]
    if claude.persona:
        flags += ["--append-system-prompt", claude.persona]
    return flags


def _window_name(handle: str, thread_key: str) -> str:
    """Readable, unique tmux window name: ``<handle>-<short>``.

    The handle makes it recognizable when attached; the thread-key suffix
    keeps it unique when the same handle opens more than one thread.
    """
    base = (re.sub(r"[^A-Za-z0-9-]", "", handle) or "s")[:24]
    suffix = re.sub(r"[^A-Za-z0-9]", "", thread_key)[-4:] or "0"
    return f"{base}-{suffix}"


class _BaseBackend:
    """Shared store handling and binding lookup."""

    name = "base"

    def __init__(self, cfg: TunnelConfig, store: TunnelStore) -> None:
        """Keep config and store references."""
        self.cfg = cfg
        self.store = store

    def _require_binding(self, thread_key: str) -> ThreadRecord:
        rec = self.store.get(thread_key)
        if rec is None:
            raise BackendError(
                f"Thread {thread_key} is not bound to any published session."
            )
        if not rec.expert_session_id or not rec.project_dir:
            raise BackendError(
                f"Thread {thread_key} has an incomplete binding."
            )
        return rec

    def reap_idle(self) -> int:
        """Default: nothing to reap."""
        return 0


class HeadlessBackend(_BaseBackend):
    """`claude -p` per question; prompt on stdin, JSON result on stdout.

    Note: `--bare` is NOT added automatically — on subscription-auth setups
    it can break login ("Not logged in"), since it skips loading user
    configuration. Add it via [claude] headless_extra_args if your setup
    supports it.
    """

    name = "headless"

    def ask(self, thread_key: str, question: str) -> Answer:
        """Run one headless turn in the thread's fork (creating it on the
        first turn) and return the result text."""
        rec = self._require_binding(thread_key)
        fork = not rec.fork_session_id
        resume_id = rec.expert_session_id if fork else rec.fork_session_id

        argv = [
            self.cfg.claude.binary,
            "-p",
            "--output-format",
            "json",
            *build_claude_flags(self.cfg, resume_id, fork),
            *self.cfg.claude.headless_extra_args,
        ]
        try:
            result = subprocess.run(
                argv,
                input=question,
                capture_output=True,
                text=True,
                cwd=rec.project_dir,
                timeout=self.cfg.limits.answer_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"Timed out after {self.cfg.limits.answer_timeout_s:.0f}s"
            ) from exc
        except OSError as exc:
            raise BackendError(f"Could not run claude: {exc}") from exc

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip()[-500:]
            raise BackendError(f"claude exited {result.returncode}: {tail}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BackendError(
                f"Unparseable claude output: {result.stdout[:300]}"
            ) from exc
        if data.get("is_error"):
            raise BackendError(str(data.get("result", "unknown error"))[:500])

        fork_id = data.get("session_id") or resume_id
        rec.fork_session_id = fork_id
        self.store.upsert(rec)
        text = str(data.get("result", "")).strip()
        if not text:
            raise BackendError("Empty answer from claude")
        return Answer(text=text, fork_session_id=fork_id, new_thread=fork)

    def forget(self, thread_key: str) -> None:
        """Drop the thread mapping."""
        self.store.remove(thread_key)


class TmuxBackend(_BaseBackend):
    """Interactive forked sessions, one tmux window per thread."""

    name = "tmux"

    def __init__(self, cfg: TunnelConfig, store: TunnelStore) -> None:
        """Bind to the dedicated tmux session named in the config."""
        super().__init__(cfg, store)
        self.tmux = TmuxSession(cfg.tmux_session)

    def _launch(
        self,
        window: str,
        project_dir: str,
        resume_id: str,
        fork: bool,
        initial_prompt: Optional[str] = None,
    ) -> None:
        """Launch an interactive fork in `window`.

        With `initial_prompt`, the question is passed as claude's positional
        prompt argument, so claude auto-submits it once it is ready — no
        keystroke simulation, which sidesteps the slow-to-accept-input window
        right after a cold launch. Without it, we wait for the prompt to go
        idle (used only for warm reuse paths).
        """
        argv = [
            self.cfg.claude.binary,
            *build_claude_flags(self.cfg, resume_id, fork),
            *self.cfg.claude.tmux_extra_args,
        ]
        if initial_prompt is not None:
            argv.append(initial_prompt)
        self.tmux.kill_window(window)
        self.tmux.new_window(window, shlex.join(argv), cwd=project_dir)
        if initial_prompt is None:
            ready = self.tmux.wait_for_idle(
                window, idle_s=2.5, timeout_s=self.cfg.limits.launch_timeout_s
            )
        else:
            ready = True
            time.sleep(2.0)  # let claude spin up; it auto-submits the prompt
        if self.tmux.pane_dead(window):
            tail = self.tmux.capture(window, lines=30).strip()[-500:]
            self.tmux.kill_window(window)
            raise BackendError(f"claude exited at launch:\n{tail}")
        if not ready:
            raise BackendError(
                "Forked session did not become ready within "
                f"{self.cfg.limits.launch_timeout_s:.0f}s"
            )

    def _fork_file(self, project_dir: Path, fork_id: str) -> Optional[Path]:
        """Locate a fork's transcript by session id."""
        for path in list_session_files(project_dir, self.cfg.claude_home):
            if path.stem == fork_id:
                return path
        return None

    def ask(self, thread_key: str, question: str) -> Answer:
        """Answer a question in the thread's fork.

        Cold paths (a new fork, or a follow-up whose window was reaped) launch
        with the question as claude's initial prompt — claude auto-submits it
        once ready, sidestepping the slow-startup keystroke problem. A warm
        follow-up window is reused by pasting + Enter. The answer is read from
        the fork's JSONL transcript either way.
        """
        rec = self._require_binding(thread_key)
        window = _window_name(rec.handle, thread_key)
        project_dir = Path(rec.project_dir)
        marker = make_marker(question)
        fork = not rec.fork_session_id

        if fork:
            before = {
                p.stem
                for p in list_session_files(project_dir, self.cfg.claude_home)
            }
            self._launch(
                window,
                rec.project_dir,
                rec.expert_session_id,
                fork=True,
                initial_prompt=question,
            )
            fork_file = wait_for_new_session_file(
                project_dir,
                before=before,
                exclude=self.store.known_fork_ids(),
                deadline=time.time() + 90,
                claude_home=self.cfg.claude_home,
            )
            if fork_file is None:
                raise BackendError(
                    "Fork transcript did not appear — did the fork launch?"
                )
        else:
            fork_file = self._fork_file(project_dir, rec.fork_session_id)
            if fork_file is None:
                raise BackendError(
                    f"Fork transcript {rec.fork_session_id} not found"
                )
            if self.tmux.window_alive(window) and not self.tmux.pane_dead(
                window
            ):
                # Warm session: paste + Enter (no startup delay when warm).
                if not self.tmux.submit_text(window, question):
                    raise BackendError(
                        "Question pasted but never submitted (Enter not "
                        "accepted)."
                    )
            else:
                # Window was reaped — relaunch cold with the prompt arg.
                self._launch(
                    window,
                    rec.project_dir,
                    rec.fork_session_id,
                    fork=False,
                    initial_prompt=question,
                )

        deadline = time.time() + self.cfg.limits.answer_timeout_s
        while True:
            if time.time() > deadline:
                raise BackendError(
                    "Timed out waiting for the answer "
                    f"({self.cfg.limits.answer_timeout_s:.0f}s)"
                )
            if self.tmux.pane_dead(window):
                tail = self.tmux.capture(window, lines=30).strip()[-500:]
                raise BackendError(f"Forked session died:\n{tail}")
            complete, text = extract_answer(fork_file, marker)
            if complete and text:
                break
            time.sleep(2.0)

        rec.fork_session_id = fork_file.stem
        rec.tmux_window = window
        self.store.upsert(rec)
        return Answer(
            text=text.strip(),
            fork_session_id=fork_file.stem,
            new_thread=fork,
        )

    def forget(self, thread_key: str) -> None:
        """Drop the mapping and kill its window."""
        rec = self.store.remove(thread_key)
        if rec is not None and rec.tmux_window:
            self.tmux.kill_window(rec.tmux_window)

    def reap_idle(self) -> int:
        """Kill windows idle longer than the configured TTL (backstop).

        A TTL of 0 (or less) disables reaping entirely — threads then live
        until closed with !done, ``forget``, or a server kill.
        """
        ttl_s = self.cfg.limits.pane_idle_ttl_min * 60
        if ttl_s <= 0:
            return 0
        reaped = 0
        for rec in self.store.all_records():
            if not rec.tmux_window or rec.backend != self.name:
                continue
            if time.time() - rec.last_used < ttl_s:
                continue
            if self.tmux.window_alive(rec.tmux_window):
                self.tmux.kill_window(rec.tmux_window)
                reaped += 1
            rec.tmux_window = ""
            self.store.upsert(rec)
        return reaped


def make_backend(cfg: TunnelConfig, store: TunnelStore) -> Backend:
    """Instantiate the configured backend."""
    if cfg.backend == "headless":
        return HeadlessBackend(cfg, store)
    return TmuxBackend(cfg, store)
