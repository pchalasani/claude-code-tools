#!/usr/bin/env python3
"""
Remote Tmux Controller

Enables tmux-cli to work when run outside of tmux by:
- Auto-creating a detached tmux session on first use
- Managing commands in separate tmux windows (not panes)
- Providing an API compatible with the local (pane) controller
"""

import subprocess
import time
import hashlib
from typing import Optional, List, Dict, Tuple, Union, Any


class RemoteTmuxController:
    """Remote controller that manages a dedicated tmux session and windows."""
    
    def __init__(self, session_name: str = "remote-cli-session"):
        """Initialize with session name and ensure the session exists."""
        self.session_name = session_name
        self.target_window: Optional[str] = None  # e.g., "session:0" (active pane in that window)
        print(f"Note: tmux-cli is running outside tmux. Managing windows in session '{session_name}'.")
        print("For better integration, consider running from inside a tmux session.")
        print("Use 'tmux-cli attach' to view the remote session.")
        self._ensure_session()
    
    # ----------------------------
    # Internal utilities
    # ----------------------------
    def _run_tmux(self, args: List[str]) -> Tuple[str, int]:
        result = subprocess.run(
            ['tmux'] + args,
            capture_output=True,
            text=True
        )
        return result.stdout.strip(), result.returncode
    
    def _ensure_session(self) -> None:
        """Create the session if it doesn't exist (detached)."""
        _, code = self._run_tmux(['has-session', '-t', self.session_name])
        if code != 0:
            # Create a detached session using user's default shell
            # Return the session name just to force creation
            self._run_tmux([
                'new-session', '-d', '-s', self.session_name, '-P', '-F', '#{session_name}'
            ])
            # Remember first window as default target
            self.target_window = f"{self.session_name}:0"
        else:
            # If already exists and we don't have a target, set to active window
            if not self.target_window:
                win, code2 = self._run_tmux(['display-message', '-p', '-t', self.session_name, '#{session_name}:#{window_index}'])
                if code2 == 0 and win:
                    self.target_window = win
    
    def _window_target(self, pane: Optional[str]) -> str:
        """Resolve user-provided pane/window hint to a tmux target.
        Accepts:
        - None -> use last target window if set else active window in session
        - digits (e.g., "1") -> session:index
        - full tmux target (e.g., "name:1" or "name:1.0" or "%12") -> pass-through
        """
        self._ensure_session()
        if pane is None:
            if self.target_window:
                return self.target_window
            # Fallback to active window in session
            win, code = self._run_tmux(['display-message', '-p', '-t', self.session_name, '#{session_name}:#{window_index}'])
            if code == 0 and win:
                self.target_window = win
                return win
            # Final fallback: session:0
            return f"{self.session_name}:0"
        # If user supplied a simple index
        if isinstance(pane, str) and pane.isdigit():
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
        self._ensure_session()
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
        self._ensure_session()
        args = ['new-window', '-t', self.session_name, '-P', '-F', '#{session_name}:#{window_index}']
        if name:
            args.extend(['-n', name])
        if command:
            args.append(command)
        out, code = self._run_tmux(args)
        if code == 0 and out:
            self.target_window = out
            return out
        return None
    
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
        # Ensure the target refers to a window (not a %pane id)
        # If user passed a pane id like %12, tmux can still resolve to its window
        self._run_tmux(['kill-window', '-t', target])
        if self.target_window == target:
            self.target_window = None
    
    def attach_session(self):
        self._ensure_session()
        # Attach will replace the current terminal view until the user detaches
        subprocess.run(['tmux', 'attach-session', '-t', self.session_name])
    
    def cleanup_session(self):
        self._run_tmux(['kill-session', '-t', self.session_name])
        self.target_window = None
    
    def list_windows(self) -> List[Dict[str, str]]:
        """List all windows in the managed session with basic info."""
        self._ensure_session()
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
