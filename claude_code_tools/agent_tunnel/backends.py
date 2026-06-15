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
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from .config import TunnelConfig, resolve_tools
from .paths import (
    changed_files,
    ensure_outbox,
    outbox_dir_for,
    snapshot_dir,
    uploads_dir_for,
)
from .session import (
    extract_answer,
    list_session_files,
    make_marker,
    wait_for_new_session_file,
)
from .store import ThreadRecord, TunnelStore
from .tmux import TmuxSession
from .trust import (
    default_trust_config_path,
    ensure_folder_trusted,
    trust_config_path_for,
)


class BackendError(RuntimeError):
    """Answering a question failed; message is user-presentable."""


@dataclass
class Answer:
    """A completed answer from a fork."""

    text: str
    fork_session_id: str
    new_thread: bool
    # Deliverable files the fork wrote into its outbox this turn (write/bash
    # handles only); the Discord layer posts them back as attachments.
    attachments: list[Path] = field(default_factory=list)


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
    cfg: TunnelConfig,
    resume_id: str,
    fork: bool,
    access: str = "read",
    add_dirs: tuple[str, ...] = (),
    extra_system: str = "",
) -> list[str]:
    """Common claude CLI flags for a fork invocation/launch.

    Args:
        cfg: Tunnel configuration (tool restrictions, model, persona).
        resume_id: Session id to resume (expert id when forking, else the
            fork's own id).
        fork: Whether to create a new fork of `resume_id`.
        access: Per-handle access level ("read"/"write"/"bash") set via >share.
        add_dirs: Extra directories the fork may access (``--add-dir``), e.g.
            the thread's inbound-attachment dir which lives outside the project.
        extra_system: Text appended to the persona system prompt (e.g. the
            per-thread outbox instruction for write/bash handles).

    Returns:
        Argument list (excluding the binary, -p, and the prompt).
    """
    claude = cfg.claude
    allowed, disallowed = resolve_tools(claude, access)
    flags = ["--resume", resume_id]
    if fork:
        flags.append("--fork-session")
    if allowed:
        flags += ["--allowedTools", ",".join(allowed)]
    if disallowed:
        flags += ["--disallowedTools", ",".join(disallowed)]
    if claude.permission_mode:
        flags += ["--permission-mode", claude.permission_mode]
    if claude.model:
        flags += ["--model", claude.model]
    for directory in add_dirs:
        flags += ["--add-dir", directory]
    system = claude.persona
    if extra_system:
        system = f"{system}\n\n{extra_system}" if system else extra_system
    if system:
        flags += ["--append-system-prompt", system]
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

    def _home(self, rec: ThreadRecord) -> Optional[Path]:
        """Claude config dir the session lives under (for transcript lookup)."""
        return Path(rec.config_dir) if rec.config_dir else self.cfg.claude_home

    def _env(self, rec: ThreadRecord) -> dict[str, str]:
        """Subprocess env with CLAUDE_CONFIG_DIR pinned to the session's dir."""
        env = dict(os.environ)
        if rec.config_dir:
            env["CLAUDE_CONFIG_DIR"] = rec.config_dir
        return env

    def _state_dir(self) -> Path:
        """Tunnel state dir (parent of state.json), home of upload dirs."""
        return self.cfg.state_path.parent

    def _uploads(self, rec: ThreadRecord) -> Path:
        """The thread's inbound-attachment dir (created if absent)."""
        upload_dir = uploads_dir_for(self._state_dir(), rec.thread_key)
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    def _can_write(self, rec: ThreadRecord) -> bool:
        """True if this handle may produce deliverables (write or bash)."""
        return rec.access in ("write", "bash")

    def _begin_turn(
        self, rec: ThreadRecord
    ) -> tuple[tuple[str, ...], str, Optional[Path], dict[str, float]]:
        """Set up attachment I/O for a turn.

        Returns ``(add_dirs, extra_system, outbox, snapshot)``:

        - ``add_dirs``: dirs to expose via ``--add-dir``. The inbound uploads
          dir is always included so a file a colleague drops into a *warm*
          window mid-thread stays readable (the dir was granted at launch).
        - ``extra_system``: outbox instruction appended to the persona
          (write/bash handles only).
        - ``outbox``: the thread's per-thread outbox dir, or None for read.
        - ``snapshot``: pre-turn file→mtime map of the outbox, for diffing.
        """
        uploads = self._uploads(rec)
        add_dirs: tuple[str, ...] = (str(uploads),)
        extra_system = ""
        outbox: Optional[Path] = None
        snapshot: dict[str, float] = {}
        if self._can_write(rec):
            outbox = ensure_outbox(Path(rec.project_dir), rec.thread_key)
            extra_system = (
                "To hand a file back to the teammate, save it into this "
                f"outbox directory:\n{outbox}\nOnly files you place there are "
                "delivered to chat; nothing else you read or edit is sent. "
                "Prefer chat-friendly formats (Markdown, CSV, plain text)."
            )
            snapshot = snapshot_dir(outbox)
        return add_dirs, extra_system, outbox, snapshot

    def _end_turn(
        self, outbox: Optional[Path], snapshot: dict[str, float]
    ) -> list[Path]:
        """Files the fork created/updated in its outbox during the turn."""
        if outbox is None:
            return []
        return changed_files(outbox, snapshot)

    def _cleanup_dirs(self, rec: Optional[ThreadRecord]) -> None:
        """Best-effort removal of a thread's upload + outbox directories."""
        if rec is None:
            return
        shutil.rmtree(
            uploads_dir_for(self._state_dir(), rec.thread_key),
            ignore_errors=True,
        )
        if rec.project_dir:
            shutil.rmtree(
                outbox_dir_for(Path(rec.project_dir), rec.thread_key),
                ignore_errors=True,
            )

    def forget(self, thread_key: str) -> None:
        """Drop the thread mapping and clean up its attachment dirs."""
        rec = self.store.remove(thread_key)
        self._cleanup_dirs(rec)

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
        add_dirs, extra_system, outbox, snapshot = self._begin_turn(rec)

        argv = [
            self.cfg.claude.binary,
            "-p",
            "--output-format",
            "json",
            *build_claude_flags(
                self.cfg, resume_id, fork, rec.access, add_dirs, extra_system
            ),
            *self.cfg.claude.headless_extra_args,
        ]
        try:
            result = subprocess.run(
                argv,
                input=question,
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
        return Answer(
            text=text,
            fork_session_id=fork_id,
            new_thread=fork,
            attachments=self._end_turn(outbox, snapshot),
        )


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
        config_dir: str = "",
        access: str = "read",
        initial_prompt: Optional[str] = None,
        add_dirs: tuple[str, ...] = (),
        extra_system: str = "",
    ) -> None:
        """Launch an interactive fork in `window`.

        `config_dir` pins the fork to the session's Claude config dir (via
        `CLAUDE_CONFIG_DIR`) so it finds the transcript and the folder's trust
        — essential when the daemon runs under a different config dir than the
        shared session (e.g. work vs personal).

        `add_dirs`/`extra_system` carry the attachment wiring (inbound upload
        dir to `--add-dir`, outbox instruction appended to the persona). They
        apply only on a cold launch; a warm window keeps what it launched with.

        With `initial_prompt`, the question is passed as claude's positional
        prompt argument, so claude auto-submits it once it is ready — no
        keystroke simulation, which sidesteps the slow-to-accept-input window
        right after a cold launch. Without it, we wait for the prompt to go
        idle (used only for warm reuse paths).
        """
        argv = [
            self.cfg.claude.binary,
            *build_claude_flags(
                self.cfg, resume_id, fork, access, add_dirs, extra_system
            ),
            *self.cfg.claude.tmux_extra_args,
        ]
        if initial_prompt is not None:
            argv.append(initial_prompt)
        if config_dir:
            argv = ["env", f"CLAUDE_CONFIG_DIR={config_dir}", *argv]
        if self.cfg.claude.auto_trust:
            self._pretrust(project_dir, config_dir)
        self.tmux.kill_window(window)
        self.tmux.new_window(window, shlex.join(argv), cwd=project_dir)
        if initial_prompt is None:
            ready = self.tmux.wait_for_idle(
                window, idle_s=2.5, timeout_s=self.cfg.limits.launch_timeout_s
            )
        else:
            ready = True
            time.sleep(2.0)  # let claude spin up; it auto-submits the prompt
        screen = self.tmux.capture(window, lines=40).lower()
        if "trust the files" in screen or "do you trust" in screen:
            self.tmux.kill_window(window)
            raise BackendError(
                "Project folder is not trusted and auto-trust did not take. "
                "Open it once in Claude and accept the prompt, or check "
                "[claude] auto_trust / trust_config_path."
            )
        if self.tmux.pane_dead(window):
            tail = self.tmux.capture(window, lines=30).strip()[-500:]
            self.tmux.kill_window(window)
            raise BackendError(f"claude exited at launch:\n{tail}")
        if not ready:
            raise BackendError(
                "Forked session did not become ready within "
                f"{self.cfg.limits.launch_timeout_s:.0f}s"
            )

    def _pretrust(self, project_dir: str, config_dir: str = "") -> None:
        """Best-effort: mark the project folder trusted before launching,
        in the same config dir the fork will use."""
        try:
            if self.cfg.claude.trust_config_path:
                path = Path(self.cfg.claude.trust_config_path).expanduser()
            elif config_dir:
                path = trust_config_path_for(config_dir)
            else:
                path = default_trust_config_path()
            ensure_folder_trusted(Path(project_dir).resolve(), path)
        except Exception:
            pass  # if it fails, the trust-dialog check below reports it

    def _fork_file(
        self, project_dir: Path, fork_id: str, claude_home: Optional[Path]
    ) -> Optional[Path]:
        """Locate a fork's transcript by session id."""
        for path in list_session_files(project_dir, claude_home):
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
        home = self._home(rec)
        marker = make_marker(question)
        fork = not rec.fork_session_id
        add_dirs, extra_system, outbox, snapshot = self._begin_turn(rec)

        if fork:
            before = {
                p.stem for p in list_session_files(project_dir, home)
            }
            self._launch(
                window,
                rec.project_dir,
                rec.expert_session_id,
                fork=True,
                config_dir=rec.config_dir,
                access=rec.access,
                initial_prompt=question,
                add_dirs=add_dirs,
                extra_system=extra_system,
            )
            fork_file = wait_for_new_session_file(
                project_dir,
                before=before,
                exclude=self.store.known_fork_ids(),
                deadline=time.time() + 90,
                claude_home=home,
            )
            if fork_file is None:
                raise BackendError(
                    "Fork transcript did not appear — did the fork launch?"
                )
        else:
            fork_file = self._fork_file(project_dir, rec.fork_session_id, home)
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
                    config_dir=rec.config_dir,
                    access=rec.access,
                    initial_prompt=question,
                    add_dirs=add_dirs,
                    extra_system=extra_system,
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
            attachments=self._end_turn(outbox, snapshot),
        )

    def forget(self, thread_key: str) -> None:
        """Drop the mapping, kill its window, and clean its attachment dirs."""
        rec = self.store.remove(thread_key)
        if rec is not None and rec.tmux_window:
            self.tmux.kill_window(rec.tmux_window)
        self._cleanup_dirs(rec)

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
