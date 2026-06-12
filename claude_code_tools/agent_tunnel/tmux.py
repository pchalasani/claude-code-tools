"""Minimal tmux operations for the agent-tunnel tmux backend.

Runs on a DEDICATED tmux server (its own socket via ``tmux -L <socket>``),
completely separate from the user's main tmux server. This isolates it from
the main server's file-descriptor budget (macOS caps a process at ~256 open
files, which a busy main server with many sessions can exhaust) and keeps
agent-tunnel's fork windows out of the user's normal ``tmux ls``. It can
neither see nor affect sessions on the main server.

Manages a detached session (default name "agent-tunnel") on that private
server with one window per conversation thread. Windows are addressed by
exact name (stable across reaping, unlike indices), created with an explicit
working directory, and receive questions via bracketed paste so multi-line
text does not submit prematurely.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from typing import Optional


class TmuxError(RuntimeError):
    """A tmux operation failed."""


class TmuxSession:
    """Operations on windows inside one dedicated tmux session."""

    def __init__(
        self, session: str = "agent-tunnel", socket: Optional[str] = None
    ) -> None:
        """Remember the session name and private socket.

        Args:
            session: tmux session name (created lazily).
            socket: tmux server socket name (``-L``); defaults to the session
                name, giving agent-tunnel its own isolated server.
        """
        self.session = session
        self.socket = socket or session

    def _run(
        self, args: list[str], stdin: Optional[str] = None
    ) -> tuple[str, int]:
        # -L runs against a private tmux server (own socket), never the
        # user's default/main server.
        result = subprocess.run(
            ["tmux", "-L", self.socket] + args,
            input=stdin,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip(), result.returncode

    def _target(self, window: str) -> str:
        """Exact-match target for a window in our session."""
        return f"={self.session}:={window}"

    def ensure_session(self) -> None:
        """Create the detached session if it does not exist."""
        _, code = self._run(["has-session", "-t", f"={self.session}"])
        if code != 0:
            _, code = self._run(
                ["new-session", "-d", "-s", self.session]
            )
            if code != 0:
                raise TmuxError(
                    f"Could not create tmux session {self.session!r}"
                )

    def list_windows(self) -> list[str]:
        """Names of all windows in the session ([] if session absent)."""
        out, code = self._run(
            [
                "list-windows",
                "-t",
                f"={self.session}",
                "-F",
                "#{window_name}",
            ]
        )
        if code != 0 or not out:
            return []
        return out.split("\n")

    def window_alive(self, window: str) -> bool:
        """True if a window with this exact name exists."""
        return window in self.list_windows()

    def new_window(self, window: str, command: str, cwd: str) -> None:
        """Create a named background window running `command` in `cwd`.

        The window is set to remain-on-exit so a crashed command leaves its
        output capturable for diagnostics.
        """
        self.ensure_session()
        _, code = self._run(
            [
                "new-window",
                "-d",
                "-t",
                f"={self.session}:",
                "-n",
                window,
                "-c",
                cwd,
                command,
            ]
        )
        if code != 0:
            raise TmuxError(f"Could not create window {window!r}")
        self._run(
            [
                "set-option",
                "-w",
                "-t",
                self._target(window),
                "remain-on-exit",
                "on",
            ]
        )

    def kill_window(self, window: str) -> None:
        """Kill a window if it exists."""
        if self.window_alive(window):
            self._run(["kill-window", "-t", self._target(window)])

    def pane_dead(self, window: str) -> bool:
        """True if the window's pane process has exited (remain-on-exit)."""
        out, code = self._run(
            [
                "list-panes",
                "-t",
                self._target(window),
                "-F",
                "#{pane_dead}",
            ]
        )
        return code == 0 and "1" in out.split("\n")

    def capture(self, window: str, lines: Optional[int] = None) -> str:
        """Capture visible (or trailing `lines` of) pane content."""
        args = ["capture-pane", "-t", self._target(window), "-p"]
        if lines:
            args.extend(["-S", f"-{lines}"])
        out, _ = self._run(args)
        return out

    def paste_text(self, window: str, text: str) -> None:
        """Deliver text via bracketed paste (multi-line safe, no submit)."""
        _, code = self._run(["load-buffer", "-b", "agent-tunnel", "-"], text)
        if code != 0:
            raise TmuxError("tmux load-buffer failed")
        _, code = self._run(
            [
                "paste-buffer",
                "-d",
                "-p",
                "-b",
                "agent-tunnel",
                "-t",
                self._target(window),
            ]
        )
        if code != 0:
            raise TmuxError("tmux paste-buffer failed")

    def submit_text(
        self,
        window: str,
        text: str,
        settle_idle_s: float = 1.0,
        settle_timeout_s: float = 20.0,
        retries: int = 5,
        enter_settle_s: float = 0.8,
    ) -> bool:
        """Paste `text` and submit it, robustly.

        Mirrors tmux-cli's proven approach: deliver the text, wait for the
        pane to actually go idle (so the TUI has finished rendering the
        paste), THEN press Enter and verify the pane reacted, retrying with
        backoff. Sending Enter before the paste settles is the main cause of
        an un-submitted question.

        Returns:
            True if the input was accepted (pane changed after Enter).
        """
        self.paste_text(window, text)
        # Let the pasted text fully render before we try to submit it.
        self.wait_for_idle(
            window, idle_s=settle_idle_s, timeout_s=settle_timeout_s
        )
        target = self._target(window)
        before = self.capture(window, lines=30)
        for attempt in range(retries):
            self._run(["send-keys", "-t", target, "Enter"])
            time.sleep(enter_settle_s)
            if self.capture(window, lines=30) != before:
                return True
            time.sleep(0.4 * (attempt + 1))
        return False

    def wait_for_idle(
        self,
        window: str,
        idle_s: float = 3.0,
        timeout_s: float = 600.0,
        check_interval_s: float = 0.5,
    ) -> bool:
        """Block until pane content is unchanged for `idle_s` seconds.

        Returns:
            True when idle was reached, False on timeout.
        """
        start = time.time()
        last_hash = ""
        last_change = time.time()
        while time.time() - start <= timeout_s:
            content = self.capture(window)
            digest = hashlib.md5(content.encode()).hexdigest()
            if digest != last_hash:
                last_hash = digest
                last_change = time.time()
            elif time.time() - last_change >= idle_s:
                return True
            time.sleep(check_interval_s)
        return False
