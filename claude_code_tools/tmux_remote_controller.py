#!/usr/bin/env python3
"""
Remote Tmux Controller

Enables tmux-cli to work when run outside of tmux by:
- Creating a detached tmux session when launching a managed window
- Managing commands in separate tmux windows (not panes)
- Providing an API compatible with the local (pane) controller
"""

import subprocess
import time
import hashlib
from typing import Optional, List, Dict, Tuple, Union, Any


class RemoteTmuxController:
    """Remote controller that manages a dedicated tmux session and windows."""

    _ready_option = '@tmux_cli_ready'
    
    def __init__(self, session_name: str = "remote-cli-session"):
        """Initialize with a managed session name without creating it."""
        self.session_name = session_name
        self.target_window: Optional[str] = None  # Stable window ID, e.g., "@1"
        print(f"Note: tmux-cli is running outside tmux. Managing windows in session '{session_name}'.")
        print("For better integration, consider running from inside a tmux session.")
        print("Use 'tmux-cli attach' to view the remote session.")
    
    # ----------------------------
    # Internal utilities
    # ----------------------------
    def _run_tmux(self, args: List[str], include_stderr: bool = False) -> Tuple[str, int]:
        try:
            result = subprocess.run(
                ['tmux'] + args,
                capture_output=True,
                text=True
            )
        except OSError as exc:
            raise RuntimeError(f"Could not run tmux {args[0]}: {exc}") from exc
        output = result.stdout.strip()
        if include_stderr and result.returncode != 0:
            output = "\n".join(part for part in (output, result.stderr.strip()) if part)
        return output, result.returncode

    @staticmethod
    def _session_is_missing(error: str) -> bool:
        """Recognize tmux's absent-session and absent-server diagnostics only."""
        return (
            "can't find session:" in error
            or error.startswith("no server running on ")
            or (error.startswith("error connecting to ") and "(No such file or directory)" in error)
        )

    @staticmethod
    def _tmux_failure(action: str, output: str, code: int) -> RuntimeError:
        detail = f": {output}" if output else ""
        return RuntimeError(f"tmux {action} failed (exit {code}){detail}")

    @staticmethod
    def _window_is_missing(error: str, window_id: str) -> bool:
        """Only classify a diagnostic for this exact stable ID as a vanished window."""
        return error.strip() in (
            f"no such window: {window_id}",
            f"can't find window: {window_id}",
        )

    def _managed_session_exists(self) -> bool:
        output, code = self._run_tmux(['has-session', '-t', f'={self.session_name}'], include_stderr=True)
        if code == 0:
            return True
        if self._session_is_missing(output):
            return False
        raise self._tmux_failure('has-session', output, code)
    
    def _ensure_session(self) -> None:
        """Create the managed session if needed for a new window."""
        if not self._managed_session_exists():
            # Create a detached session using user's default shell
            # Return the session name just to force creation
            output, code = self._run_tmux([
                'new-session', '-d', '-s', self.session_name, '-P', '-F', '#{session_name}'
            ], include_stderr=True)
            if code != 0:
                # Another launcher may have won the check/create race.
                if output != f'duplicate session: {self.session_name}' or not self._managed_session_exists():
                    raise self._tmux_failure('new-session', output, code)
            elif not output:
                raise self._tmux_failure('new-session', output, code)
    
    def _window_target(self, pane: Optional[str]) -> str:
        """Resolve user-provided pane/window hint to a tmux target.
        Accepts:
        - None -> use last target window if set else a ready active window in session
        - digits (e.g., "1") -> session:index
        - full tmux target (e.g., "name:1" or "name:1.0" or "%12") -> pass-through
        """
        if pane is None:
            if self.target_window:
                # Window IDs can be reused after a server restart. Check both
                # membership and readiness before trusting a cached default.
                windows, code = self._run_tmux(
                    ['list-windows', '-t', f'={self.session_name}',
                     '-F', '#{window_id}|#{@tmux_cli_ready}'],
                    include_stderr=True,
                )
                if code != 0 and not self._session_is_missing(windows):
                    raise self._tmux_failure('list-windows', windows, code)
                expected = f'{self.target_window}|{self.target_window}'
                if code == 0 and expected in windows.splitlines():
                    return self.target_window
                self.target_window = None
                if code != 0:
                    raise ValueError(
                        "No target pane/window specified; managed session "
                        f"'{self.session_name}' does not exist. "
                        "Launch a window or pass --pane."
                    )
            # An existing managed session may have been created by an earlier CLI call.
            win, code = self._run_tmux(
                ['display-message', '-p', '-t', f'={self.session_name}:',
                 '#{window_id}|#{@tmux_cli_ready}'],
                include_stderr=True,
            )
            if code == 0:
                if not win:
                    raise self._tmux_failure('display-message', win, code)
                window_id, separator, ready = win.partition('|')
                if not separator or not window_id.startswith('@'):
                    raise self._tmux_failure('display-message', win, code)
                if ready == window_id:
                    self.target_window = window_id
                    return window_id
                raise ValueError(
                    f"No target pane/window specified; managed session '{self.session_name}' "
                    "has no successfully launched active window. Launch a window or pass --pane."
                )
            if code != 0 and not (self._session_is_missing(win) or "can't find window:" in win):
                raise self._tmux_failure('display-message', win, code)
            raise ValueError(
                f"No target pane/window specified; managed session '{self.session_name}' "
                "does not exist or has no active window. Launch a window or pass --pane."
            )
        # If user supplied a simple index
        if isinstance(pane, bool):
            raise ValueError("Boolean --pane is not a window index")
        if type(pane) is int or (isinstance(pane, str) and pane.isdigit()):
            if not self._managed_session_exists():
                raise ValueError(
                    f"Managed session '{self.session_name}' does not exist; "
                    "launch a window or pass a full --pane target."
                )
            return f"{self.session_name}:{pane}"
        # Otherwise assume user provided a pane/window target or pane id
        return pane
    
    def _active_pane_in_window(self, window_target: str) -> str:
        """Return a target that tmux can use to address the active pane of a window.
        For tmux commands that accept pane targets, a window target resolves to its
        active pane, so we can pass the window target directly.
        Still, normalize to make intent clear.
        """
        return window_target
    
    def list_panes(self) -> List[Dict[str, str]]:
        """In remote mode, list windows in the managed session.
        Returns a list shaped similarly to local list_panes, with keys:
        id (window target), index, title (window name), active (bool), size (N/A)
        """
        out, code = self._run_tmux([
            'list-windows', '-t', self.session_name,
            '-F', '#{window_index}|#{window_name}|#{window_active}|#{window_width}x#{window_height}'
        ])
        if code != 0 or not out:
            return []
        windows: List[Dict[str, str]] = []
        for line in out.split('\n'):
            if not line:
                continue
            idx, name, active, size = line.split('|')
            windows.append({
                'id': f"{self.session_name}:{idx}",
                'index': idx,
                'title': name,
                'active': active == '1',
                'size': size
            })
        return windows
    
    def launch_cli(self, command: str, name: Optional[str] = None) -> Optional[str]:
        """Launch a command in a new window within the managed session.
        Returns the window target (e.g., "session:1").
        """
        previous_target = self.target_window
        try:
            self._ensure_session()
            args = ['new-window', '-t', f'={self.session_name}:', '-P', '-F',
                    '#{session_name}:#{window_index}|#{window_id}']
            if name:
                args.extend(['-n', name])
            if command:
                args.append(command)
            out, code = self._run_tmux(args, include_stderr=True)
            if code != 0 or not out:
                raise self._tmux_failure('new-window', out, code)
            target, separator, window_id = out.partition('|')
            if not separator or not target or not window_id.startswith('@'):
                raise self._tmux_failure('new-window', out, code)
            try:
                marked, mark_code = self._run_tmux(
                    ['set-option', '-w', '-t', window_id, self._ready_option, window_id],
                    include_stderr=True,
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Ready mark for created window {window_id} could not be set; "
                    f"command may have started. {exc}"
                ) from exc
            if mark_code != 0:
                if self._window_is_missing(marked, window_id):
                    raise RuntimeError(
                        f"Command was launched in window {window_id}, but that window "
                        f"disappeared before its ready mark; no live target is available "
                        f"(tmux set-option exit {mark_code}: {marked}). "
                        "The command may have run; do not retry automatically."
                    )
                raise RuntimeError(
                    f"{self._tmux_failure('set-option', marked, mark_code)}; "
                    f"created window {window_id}; command may have started. "
                    "Inspect the window before retrying."
                )
            self.target_window = window_id
            return target
        except Exception:
            self.target_window = previous_target
            raise
    
    def send_keys(self, text: str, pane_id: Optional[str] = None, enter: bool = True,
                  delay_enter: Union[bool, float] = True, verify_enter: bool = True,
                  max_retries: int = 3):
        """Send keys to the active pane of a given window (or last target).

        Args:
            text: Text to send
            pane_id: Target pane/window
            enter: Whether to send Enter key after text
            delay_enter: If True, use 1.5s delay; if float, use that delay in seconds
            verify_enter: If True, verify Enter was received and retry if not
            max_retries: Maximum number of Enter key retries
        """
        if not text:
            return
        target = self._active_pane_in_window(self._window_target(pane_id))
        if enter and delay_enter:
            # First send text (no Enter)
            self._run_tmux(['send-keys', '-t', target, text])
            # Delay
            delay = 1.5 if isinstance(delay_enter, bool) else float(delay_enter)
            time.sleep(delay)
            # Capture pane state AFTER text is sent but BEFORE Enter
            # This ensures we detect changes caused by Enter, not by the text itself
            content_before_enter = self.capture_pane(pane_id, lines=20) if verify_enter else None
            # Send Enter with verification and retry
            self._send_enter_with_retry(target, pane_id, content_before_enter, verify_enter, max_retries)
        else:
            args = ['send-keys', '-t', target, text]
            if enter:
                args.append('Enter')
            self._run_tmux(args)

    def _send_enter_with_retry(self, target: str, pane_id: Optional[str],
                                content_before_enter: Optional[str], verify: bool,
                                max_retries: int):
        """Send Enter key with optional verification and retry.

        Args:
            target: Resolved tmux target
            pane_id: Original pane_id for capture_pane
            content_before_enter: Pane content captured after text but before Enter
            verify: Whether to verify Enter was received
            max_retries: Maximum retry attempts
        """
        for attempt in range(max_retries):
            # Send Enter
            self._run_tmux(['send-keys', '-t', target, 'Enter'])

            if not verify or content_before_enter is None:
                return

            # Wait a bit for the command to process
            time.sleep(0.3)

            # Check if pane content changed
            content_after = self.capture_pane(pane_id, lines=20)

            if content_after != content_before_enter:
                return  # Enter was successful

            # Content unchanged - retry with exponential backoff
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
    
    def capture_pane(self, pane_id: Optional[str] = None, lines: Optional[int] = None) -> str:
        """Capture output from the active pane of a window."""
        target = self._active_pane_in_window(self._window_target(pane_id))
        args = ['capture-pane', '-t', target, '-p']
        if lines:
            args.extend(['-S', f'-{lines}'])
        out, _ = self._run_tmux(args)
        return out
    
    def wait_for_idle(self, pane_id: Optional[str] = None, idle_time: float = 2.0,
                     check_interval: float = 0.5, timeout: Optional[int] = None) -> bool:
        """Wait until captured output is unchanged for idle_time seconds."""
        target = self._active_pane_in_window(self._window_target(pane_id))
        start_time = time.time()
        last_change = time.time()
        last_hash = ""
        while True:
            if timeout is not None and (time.time() - start_time) > timeout:
                return False
            content, _ = self._run_tmux(['capture-pane', '-t', target, '-p'])
            h = hashlib.md5(content.encode()).hexdigest()
            if h != last_hash:
                last_hash = h
                last_change = time.time()
            else:
                if (time.time() - last_change) >= idle_time:
                    return True
            time.sleep(check_interval)
    
    def send_interrupt(self, pane_id: Optional[str] = None):
        target = self._active_pane_in_window(self._window_target(pane_id))
        self._run_tmux(['send-keys', '-t', target, 'C-c'])
    
    def send_escape(self, pane_id: Optional[str] = None):
        target = self._active_pane_in_window(self._window_target(pane_id))
        self._run_tmux(['send-keys', '-t', target, 'Escape'])

    def execute(self, command: str, pane_id: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
        """
        Execute a command and return output with exit code.

        Uses unique markers to capture the command's exit status reliably.

        Args:
            command: Shell command to execute
            pane_id: Target window/pane (uses self.target_window if not specified)
            timeout: Maximum seconds to wait for completion (default: 30)

        Returns:
            Dict with keys:
                - output (str): Command output (stdout + stderr)
                - exit_code (int): Command exit status, or -1 on timeout
        """
        from .tmux_execution_helpers import (
            generate_execution_markers,
            wrap_command_with_markers,
            poll_for_completion,
        )

        # Generate unique markers for this execution
        start_marker, end_marker = generate_execution_markers()

        # Wrap command with markers
        wrapped_command = wrap_command_with_markers(command, start_marker, end_marker)

        # Send wrapped command to pane
        self.send_keys(wrapped_command, pane_id=pane_id, enter=True, delay_enter=False)

        # Poll for completion with progressive expansion
        return poll_for_completion(
            capture_fn=lambda lines: self.capture_pane(pane_id=pane_id, lines=lines),
            start_marker=start_marker,
            end_marker=end_marker,
            timeout=timeout,
        )

    def kill_window(self, window_id: Optional[str] = None):
        target = self._window_target(window_id)
        cached_target = target
        if self.target_window and target != self.target_window:
            resolved, code = self._run_tmux(
                ['display-message', '-p', '-t', target, '#{window_id}']
            )
            if code == 0 and resolved:
                cached_target = resolved
        # Ensure the target refers to a window (not a %pane id)
        # If user passed a pane id like %12, tmux can still resolve to its window
        self._run_tmux(['kill-window', '-t', target])
        if self.target_window == cached_target:
            self.target_window = None
    
    def attach_session(self):
        if not self._managed_session_exists():
            raise ValueError(
                f"Managed session '{self.session_name}' does not exist; "
                "launch a window before attaching."
            )
        # Attach will replace the current terminal view until the user detaches
        subprocess.run(['tmux', 'attach-session', '-t', self.session_name])
    
    def cleanup_session(self):
        self._run_tmux(['kill-session', '-t', self.session_name])
        self.target_window = None
    
    def list_windows(self) -> List[Dict[str, str]]:
        """List all windows in the managed session with basic info."""
        out, code = self._run_tmux(['list-windows', '-t', self.session_name, '-F', '#{window_index}|#{window_name}|#{window_active}'])
        if code != 0 or not out:
            return []
        windows: List[Dict[str, str]] = []
        for line in out.split('\n'):
            if not line:
                continue
            idx, name, active = line.split('|')
            # Try to get active pane id for each window (best effort)
            pane_out, _ = self._run_tmux(['display-message', '-p', '-t', f'{self.session_name}:{idx}', '#{pane_id}'])
            windows.append({
                'index': idx,
                'name': name,
                'active': active == '1',
                'pane_id': pane_out or ''
            })
        return windows
    
    def _resolve_pane_id(self, pane: Optional[str]) -> Optional[str]:
        """Resolve user-provided identifier to a tmux target string for remote ops."""
        return self._window_target(pane)
