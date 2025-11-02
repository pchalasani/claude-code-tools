#!/usr/bin/env python3
"""
Tmux CLI Controller for Claude Code
This script provides functions to interact with CLI applications running in tmux panes.
"""

import subprocess
import time
import re
from typing import Optional, List, Dict, Tuple, Callable, Union
import json
import os
import hashlib
import importlib.resources


def _load_help_text():
    """Load help text from the package's docs directory."""
    try:
        # For development, try to load from the actual file system first
        import pathlib
        module_dir = pathlib.Path(__file__).parent
        # Try looking in the parent directory (repo root) for docs
        docs_file = module_dir.parent / 'docs' / 'tmux-cli-instructions.md'
        if docs_file.exists():
            return docs_file.read_text(encoding='utf-8')
        
        # For installed packages, use importlib.resources
        if hasattr(importlib.resources, 'files'):
            # Python 3.9+ style
            import importlib.resources as resources
            
            # Try different possible locations for the docs
            # 1. Try docs as a subdirectory within the package
            try:
                help_file = resources.files('claude_code_tools') / 'docs' / 'tmux-cli-instructions.md'
                if help_file.is_file():
                    return help_file.read_text(encoding='utf-8')
            except:
                pass
            
            # 2. Try accessing parent package to find docs at root level
            try:
                # This assumes docs/ is packaged at the same level as claude_code_tools/
                package_root = resources.files('claude_code_tools').parent
                help_file = package_root / 'docs' / 'tmux-cli-instructions.md'
                if help_file.is_file():
                    return help_file.read_text(encoding='utf-8')
            except:
                pass
        
        # Try pkg_resources as another fallback
        try:
            import pkg_resources
            # Try different paths
            for path in ['docs/tmux-cli-instructions.md', '../docs/tmux-cli-instructions.md']:
                try:
                    return pkg_resources.resource_string(
                        'claude_code_tools', path
                    ).decode('utf-8')
                except:
                    continue
        except:
            pass
            
    except Exception as e:
        pass
    
    # If all else fails, return a basic help message
    return """# tmux-cli Instructions

Error: Could not load full documentation.

Basic usage:
- tmux-cli launch "command" - Launch a CLI application
- tmux-cli send "text" --pane=PANE_ID - Send input to a pane
- tmux-cli capture --pane=PANE_ID - Capture output from a pane
- tmux-cli status - Show current tmux status and all panes
- tmux-cli kill --pane=PANE_ID - Kill a pane
- tmux-cli help - Display full help

Pane Identification:
- Just the pane number (e.g., '2') - refers to pane 2 in the current window
- Full format: session:window.pane (e.g., 'myapp:1.2') - for any pane in any session

For full documentation, see docs/tmux-cli-instructions.md in the package repository."""


class TmuxCLIController:
    """Controller for interacting with CLI applications in tmux windows."""
    
    def __init__(self, session_name: Optional[str] = None):
        """
        Initialize the controller.

        Args:
            session_name: Name of tmux session (defaults to current)
        """
        self.session_name = session_name
        self.target_window = None
    
    def _run_tmux_command(self, command: List[str]) -> Tuple[str, int]:
        """
        Run a tmux command and return output and exit code.
        
        Args:
            command: List of command components
            
        Returns:
            Tuple of (output, exit_code)
        """
        result = subprocess.run(
            ['tmux'] + command,
            capture_output=True,
            text=True
        )
        return result.stdout.strip(), result.returncode
    
    def get_current_session(self) -> Optional[str]:
        """Get the name of the current tmux session."""
        output, code = self._run_tmux_command(['display-message', '-p', '#{session_name}'])
        return output if code == 0 else None
    
    def get_current_window(self) -> Optional[str]:
        """Get the name of the current tmux window."""
        output, code = self._run_tmux_command(['display-message', '-p', '#{window_name}'])
        return output if code == 0 else None
    
    def get_current_pane(self) -> Optional[str]:
        """Get the ID of the current tmux pane."""
        output, code = self._run_tmux_command(['display-message', '-p', '#{pane_id}'])
        return output if code == 0 else None
    
    def get_current_pane_index(self) -> Optional[str]:
        """Get the index of the current tmux pane."""
        output, code = self._run_tmux_command(['display-message', '-p', '#{pane_index}'])
        return output if code == 0 else None
    
    def get_pane_command(self, pane_id: str) -> Optional[str]:
        """Get the command running in a specific pane."""
        output, code = self._run_tmux_command(['display-message', '-t', pane_id, '-p', '#{pane_current_command}'])
        return output if code == 0 else None
    
    def get_current_window_id(self) -> Optional[str]:
        """Get the ID of the current tmux window."""
        # Use TMUX_PANE environment variable to get the pane we're running in
        import os
        current_pane = os.environ.get('TMUX_PANE')
        if current_pane:
            # Get the window ID for this specific pane
            output, code = self._run_tmux_command(['display-message', '-t', current_pane, '-p', '#{window_id}'])
            return output if code == 0 else None
        # Fallback to current window
        output, code = self._run_tmux_command(['display-message', '-p', '#{window_id}'])
        return output if code == 0 else None
    
    # Window-based operations

    def generate_window_name(self, custom_name: Optional[str] = None) -> str:
        """
        Generate a unique window name with tmux-cli prefix.

        Args:
            custom_name: Optional custom name (will be prefixed with tmux-cli-)

        Returns:
            Unique window name like 'tmux-cli-1730559234-123' or 'tmux-cli-custom-name'
        """
        prefix = "tmux-cli"

        if custom_name:
            # Ensure custom names also have the prefix for tracking
            if not custom_name.startswith(prefix + "-"):
                return f"{prefix}-{custom_name}"
            return custom_name

        # Generate timestamp-based name
        timestamp = int(time.time())
        # Add a small random component to handle rapid consecutive calls
        import random
        rand_suffix = random.randint(0, 999)
        return f"{prefix}-{timestamp}-{rand_suffix}"

    def create_window(self, start_command: Optional[str] = None,
                     window_name: Optional[str] = None) -> Optional[str]:
        """
        Create a new window in the current session with tmux-cli prefix.

        Args:
            start_command: Command to run in the new window
            window_name: Custom window name (will be prefixed with 'tmux-cli-')
                        If not provided, generates timestamp-based name

        Returns:
            Window name of the created window (always starts with 'tmux-cli-')
        """
        # Get current session
        session = self.session_name or self.get_current_session()
        if not session:
            return None

        # Generate name with tmux-cli prefix
        name = self.generate_window_name(window_name)

        # Create window with unique name in the background (don't switch focus)
        cmd = ['new-window', '-d', '-t', session, '-n', name, '-P', '-F', '#{window_name}']

        if start_command:
            cmd.append(start_command)

        output, code = self._run_tmux_command(cmd)

        if code == 0:
            self.target_window = output
            return output
        return None

    def resolve_window_identifier(self, identifier: str) -> Optional[str]:
        """
        Convert window identifier to a tmux target.

        Supports:
        - Window IDs: @123 (most stable)
        - Window names: tmux-cli-12345
        - session:window format: mysession:tmux-cli-12345
        - Index (legacy): 2

        Returns:
            Tmux target string or None
        """
        if not identifier:
            return None

        # Convert to string if it's a number
        identifier = str(identifier)

        # Window ID format (@N) - most stable, use as-is
        if identifier.startswith('@'):
            return identifier

        # Full format with session
        if ':' in identifier:
            return identifier

        # Get current session for relative references
        session = self.session_name or self.get_current_session()
        if not session:
            return None

        # Check if it's a window name by listing windows
        output, code = self._run_tmux_command([
            'list-windows', '-t', session,
            '-F', '#{window_name}|#{window_id}'
        ])

        if code == 0 and output:
            for line in output.split('\n'):
                if line:
                    parts = line.split('|')
                    if len(parts) == 2:
                        win_name, win_id = parts
                        if win_name == identifier:
                            return f'{session}:{win_name}'

        # Legacy: treat as index
        if identifier.isdigit():
            return f'{session}:{identifier}'

        return None

    def list_windows(self) -> List[Dict[str, str]]:
        """
        List all windows in the current session.

        Returns:
            List of dicts with window info (id, index, name, active)
        """
        session = self.session_name or self.get_current_session()
        if not session:
            return []

        output, code = self._run_tmux_command([
            'list-windows', '-t', session,
            '-F', '#{window_id}|#{window_index}|#{window_name}|#{window_active}|#{pane_current_command}'
        ])

        if code != 0:
            return []

        windows = []
        for line in output.split('\n'):
            if line:
                parts = line.split('|')
                if len(parts) >= 4:
                    windows.append({
                        'id': parts[0],
                        'index': parts[1],
                        'name': parts[2],
                        'active': parts[3] == '1',
                        'command': parts[4] if len(parts) > 4 else ''
                    })
        return windows

    def list_tmux_cli_windows(self) -> List[Dict[str, str]]:
        """
        List all windows created by tmux-cli (by name prefix).

        Returns:
            List of dicts with window info
        """
        all_windows = self.list_windows()
        return [w for w in all_windows if w['name'].startswith('tmux-cli-')]

    def kill_window(self, window_id: Optional[str] = None):
        """
        Kill a window.

        Args:
            window_id: Target window (name, @id, or index)
        """
        target = window_id or self.target_window
        if not target:
            raise ValueError("No target window specified")

        # Resolve to tmux target
        resolved = self.resolve_window_identifier(target)
        if not resolved:
            resolved = target  # Try the original if resolution failed

        self._run_tmux_command(['kill-window', '-t', resolved])

        if target == self.target_window:
            self.target_window = None

    def cleanup_all_windows(self):
        """
        Kill all tmux-cli created windows.
        """
        windows = self.list_tmux_cli_windows()
        for window in windows:
            try:
                self._run_tmux_command(['kill-window', '-t', window['id']])
            except:
                pass  # Continue cleanup even if one fails

    def send_keys_to_window(self, text: str, window_id: Optional[str] = None,
                           enter: bool = True, delay_enter: Union[bool, float] = True):
        """
        Send keystrokes to a window (to its active pane).

        Args:
            text: Text to send
            window_id: Target window (uses self.target_window if not specified)
            enter: Whether to send Enter key after text
            delay_enter: If True, use 1.0s delay; if float, use that delay in seconds
        """
        target = window_id or self.target_window
        if not target:
            raise ValueError("No target window specified")

        # Resolve to tmux target
        resolved = self.resolve_window_identifier(target)
        if not resolved:
            resolved = target

        if enter and delay_enter:
            # Send text without Enter first
            cmd = ['send-keys', '-t', resolved, text]
            self._run_tmux_command(cmd)

            # Determine delay duration
            if isinstance(delay_enter, bool):
                delay = 1.0
            else:
                delay = float(delay_enter)

            # Apply delay
            time.sleep(delay)

            # Then send just Enter
            cmd = ['send-keys', '-t', resolved, 'Enter']
            self._run_tmux_command(cmd)
        else:
            cmd = ['send-keys', '-t', resolved, text]
            if enter:
                cmd.append('Enter')
            self._run_tmux_command(cmd)

    def capture_window(self, window_id: Optional[str] = None, lines: Optional[int] = None) -> str:
        """
        Capture the contents of a window (its active pane).

        Args:
            window_id: Target window (uses self.target_window if not specified)
            lines: Number of lines to capture from bottom (captures all if None)

        Returns:
            Captured text content
        """
        target = window_id or self.target_window
        if not target:
            raise ValueError("No target window specified")

        # Resolve to tmux target
        resolved = self.resolve_window_identifier(target)
        if not resolved:
            resolved = target

        cmd = ['capture-pane', '-t', resolved, '-p']

        if lines:
            cmd.extend(['-S', f'-{lines}'])

        output, _ = self._run_tmux_command(cmd)
        return output

    def wait_for_idle(self, window_id: Optional[str] = None, idle_time: float = 2.0,
                     check_interval: float = 0.5, timeout: Optional[int] = None) -> bool:
        """
        Wait for a window to become idle (no output changes for idle_time seconds).

        Args:
            window_id: Target window
            idle_time: Seconds of no change to consider idle
            check_interval: Seconds between checks
            timeout: Maximum seconds to wait (None for no timeout)

        Returns:
            True if idle detected, False if timeout
        """
        target = window_id or self.target_window
        if not target:
            raise ValueError("No target window specified")

        start_time = time.time()
        last_change_time = time.time()
        last_hash = ""

        while True:
            if timeout and (time.time() - start_time > timeout):
                return False

            content = self.capture_window(target)
            content_hash = hashlib.md5(content.encode()).hexdigest()

            if content_hash != last_hash:
                last_hash = content_hash
                last_change_time = time.time()
            elif time.time() - last_change_time >= idle_time:
                return True

            time.sleep(check_interval)

    def launch_cli(self, command: str, window_name: Optional[str] = None) -> Optional[str]:
        """
        Launch a CLI application in a new window.

        Args:
            command: Command to launch
            window_name: Custom window name (will be prefixed with 'tmux-cli-')

        Returns:
            Window name starting with 'tmux-cli-'
        """
        return self.create_window(start_command=command, window_name=window_name)


class CLI:
    """Unified CLI interface that auto-detects tmux environment.

    Automatically uses:
    - TmuxCLIController when inside tmux (for window management)
    - RemoteTmuxController when outside tmux (for window management)
    """
    
    def __init__(self, session: Optional[str] = None):
        """Initialize with auto-detection of tmux environment.
        
        Args:
            session: Optional session name for remote mode (ignored in local mode)
        """
        self.in_tmux = bool(os.environ.get('TMUX'))
        
        if self.in_tmux:
            # Inside tmux - use local controller
            self.controller = TmuxCLIController()
            self.mode = 'local'
        else:
            # Outside tmux - use remote controller
            from .tmux_remote_controller import RemoteTmuxController
            session_name = session or "remote-cli-session"
            self.controller = RemoteTmuxController(session_name=session_name)
            self.mode = 'remote'
    
    def status(self):
        """Show current tmux status and tmux-cli managed windows."""
        if not self.in_tmux:
            print("Not currently in tmux")
            if hasattr(self.controller, 'session_name'):
                print(f"Remote session: {self.controller.session_name}")
            return

        # Get current location
        session = self.controller.get_current_session()
        window = self.controller.get_current_window()

        if session and window:
            print(f"Current location: {session}:{window}")
        else:
            print("Could not determine current tmux location")

        # List tmux-cli managed windows
        tmux_cli_windows = self.controller.list_tmux_cli_windows()
        if tmux_cli_windows:
            print(f"\ntmux-cli managed windows:")
            for win in tmux_cli_windows:
                active_marker = " *" if win['active'] else "  "
                command = win.get('command', '')
                print(f"{active_marker} {win['name']:30} {command:20}")

        # List all windows in session
        all_windows = self.controller.list_windows()
        if all_windows:
            print(f"\nAll windows in session:")
            for win in all_windows:
                active_marker = " *" if win['active'] else "  "
                command = win.get('command', '')
                print(f"{active_marker} {win['index']:3} {win['name']:30} {command:20}")
    
    def launch(self, command: str, window_name: Optional[str] = None):
        """Launch a command in a new window.

        Args:
            command: Command to launch
            window_name: Custom window name (will be prefixed with 'tmux-cli-')
        """
        if self.mode == 'local':
            identifier = self.controller.launch_cli(command, window_name=window_name)
            print(f"Launched '{command}' in window: {identifier}")
        else:
            # Remote mode
            # Remote controller uses 'name' parameter
            from .tmux_remote_controller import RemoteTmuxController
            if isinstance(self.controller, RemoteTmuxController):
                identifier = self.controller.launch_cli(command, name=window_name)
            else:
                identifier = self.controller.launch_cli(command, window_name=window_name)
            print(f"Launched '{command}' in window: {identifier}")
        return identifier
    
    def send(self, text: str, window_name: Optional[str] = None,
             enter: bool = True, delay_enter: Union[bool, float] = True):
        """Send text to a window.

        Args:
            text: Text to send
            window_name: Target window name
            enter: Whether to send Enter key after text
            delay_enter: If True, use 1.0s delay; if float, use that delay in seconds
        """
        if self.mode == 'local':
            if window_name:
                resolved = self.controller.resolve_window_identifier(window_name)
                if not resolved:
                    print(f"Could not resolve window: {window_name}")
                    return
                self.controller.send_keys_to_window(text, window_id=resolved,
                                                   enter=enter, delay_enter=delay_enter)
            else:
                self.controller.send_keys_to_window(text, enter=enter, delay_enter=delay_enter)
        else:
            # Remote mode
            self.controller.send_keys(text, pane_id=window_name, enter=enter, delay_enter=delay_enter)
        print("Text sent")
    
    def capture(self, window_name: Optional[str] = None, lines: Optional[int] = None):
        """Capture window content.

        Args:
            window_name: Target window identifier (window name)
            lines: Number of lines to capture from bottom
        """
        if self.mode == 'local':
            if window_name:
                resolved = self.controller.resolve_window_identifier(window_name)
                if not resolved:
                    print(f"Could not resolve window: {window_name}")
                    return ""
                content = self.controller.capture_window(window_id=resolved, lines=lines)
            else:
                content = self.controller.capture_window(lines=lines)
        else:
            # Remote mode
            content = self.controller.capture_pane(pane_id=window_name, lines=lines)
        return content
    
    def interrupt(self, window_name: Optional[str] = None):
        """Send Ctrl+C to a window.

        Args:
            window_name: Target window identifier (window name)
        """
        if self.mode == 'local':
            if window_name:
                resolved = self.controller.resolve_window_identifier(window_name)
                if not resolved:
                    print(f"Could not resolve window: {window_name}")
                    return
                self.controller._run_tmux_command(['send-keys', '-t', resolved, 'C-c'])
            else:
                self.controller._run_tmux_command(['send-keys', '-t', self.controller.target_window, 'C-c'])
        else:
            # Remote mode
            target_id = self.controller._resolve_pane_id(window_name)
            self.controller.send_interrupt(pane_id=target_id)
        print("Sent interrupt signal")

    def escape(self, window_name: Optional[str] = None):
        """Send Escape key to a window.

        Args:
            window_name: Target window identifier (window name)
        """
        if self.mode == 'local':
            if window_name:
                resolved = self.controller.resolve_window_identifier(window_name)
                if not resolved:
                    print(f"Could not resolve window: {window_name}")
                    return
                self.controller._run_tmux_command(['send-keys', '-t', resolved, 'Escape'])
            else:
                self.controller._run_tmux_command(['send-keys', '-t', self.controller.target_window, 'Escape'])
        else:
            # Remote mode
            target_id = self.controller._resolve_pane_id(window_name)
            self.controller.send_escape(pane_id=target_id)
        print("Sent escape key")

    def kill(self, window_name: Optional[str] = None):
        """Kill a window.

        Args:
            window_name: Target window identifier (window name)
        """
        if self.mode == 'local':
            if window_name:
                resolved = self.controller.resolve_window_identifier(window_name)
                if not resolved:
                    print(f"Could not resolve window: {window_name}")
                    return
                try:
                    self.controller.kill_window(window_id=window_name)
                    print(f"Window '{window_name}' killed")
                except ValueError as e:
                    print(str(e))
            else:
                try:
                    self.controller.kill_window()
                    print("Window killed")
                except ValueError as e:
                    print(str(e))
        else:
            # Remote mode
            try:
                self.controller.kill_window(window_id=window_name)
                print("Window killed")
            except ValueError as e:
                print(str(e))
    
    def wait_idle(self, window_name: Optional[str] = None, idle_time: float = 2.0,
                  timeout: Optional[int] = None):
        """Wait for window to become idle (no output changes).

        Args:
            window_name: Target window name
            idle_time: Seconds of no change to consider idle
            timeout: Maximum seconds to wait
        """
        if self.mode == 'local':
            if window_name:
                resolved = self.controller.resolve_window_identifier(window_name)
                if not resolved:
                    print(f"Could not resolve window: {window_name}")
                    return False
                target = window_name
            else:
                target = None

            print(f"Waiting for window to become idle (no changes for {idle_time}s)...")
            if self.controller.wait_for_idle(window_id=target, idle_time=idle_time, timeout=timeout):
                print("Window is idle")
                return True
            else:
                print("Timeout waiting for idle")
                return False
        else:
            # Remote mode
            print("wait_idle not supported in remote mode")
            return False
    
    def attach(self):
        """Attach to the managed session (remote mode only)."""
        if self.mode == 'local':
            print("Attach is only available in remote mode (when outside tmux)")
            return
        self.controller.attach_session()

    def list_windows(self):
        """List all windows in the current session."""
        if self.mode == 'local':
            windows = self.controller.list_windows()
            if not windows:
                session = self.controller.get_current_session()
                print(f"No windows in session '{session}'")
                return

            session = self.controller.get_current_session()
            print(f"Windows in session '{session}':")
            for w in windows:
                active = " *" if w['active'] else "  "
                command = w.get('command', '')
                print(f"{active} {w['index']:3} {w['name']:30} {command:20}")
        else:
            # Remote mode
            windows = self.controller.list_windows()
            if not windows:
                print(f"No windows in session '{self.controller.session_name}'")
                return

            print(f"Windows in session '{self.controller.session_name}':")
            for w in windows:
                active = " (active)" if w['active'] else ""
                print(f"  {w['index']}: {w['name']}{active} - pane {w['pane_id']}")

    def cleanup(self):
        """Clean up all tmux-cli managed windows."""
        if self.mode == 'local':
            windows = self.controller.list_tmux_cli_windows()
            if not windows:
                print("No tmux-cli managed windows to clean up")
                return

            print(f"Cleaning up {len(windows)} tmux-cli window(s)...")
            self.controller.cleanup_all_windows()
            print("Cleanup complete")
        else:
            # Remote mode - kill entire session
            print("Remote mode: Use 'tmux-cli cleanup' to kill the entire session")
            self.controller.cleanup_session()
    
    def demo(self):
        """Run a demo showing tmux CLI control capabilities."""
        print("Running demo...")
        
        if self.mode == 'local':
            # Original local demo
            print("\nCurrent panes:")
            panes = self.controller.list_panes()
            for pane in panes:
                print(f"  {pane['formatted_id']}: {pane['command']} - {pane['title']}")
            
            # Create a new pane with Python REPL
            print("\nCreating new pane with Python...")
            pane_id = self.controller.launch_cli('python3')
            print(f"Created pane: {pane_id}")
            
            # Wait for Python prompt
            time.sleep(1)
            if self.controller.wait_for_prompt('>>>', timeout=5):
                print("Python prompt detected")
                
                # Send a command
                print("\nSending Python command...")
                self.controller.send_keys('print("Hello from tmux!")')
                time.sleep(0.5)
                
                # Capture output
                output = self.controller.capture_pane(lines=10)
                print(f"\nCaptured output:\n{output}")
                
                # Clean up
                print("\nCleaning up...")
                self.controller.send_keys('exit()')
                time.sleep(0.5)
                self.controller.kill_pane()
                print("Demo complete!")
            else:
                print("Failed to detect Python prompt")
                self.controller.kill_pane()
        else:
            # Remote demo
            print("\nCreating new window with Python...")
            pane_id = self.launch('python3', name='demo-python')
            
            # Wait for idle (Python prompt)
            time.sleep(1)
            if self.wait_idle(pane=pane_id, idle_time=1.0, timeout=5):
                print("Python is ready")
                
                # Send a command
                print("\nSending Python command...")
                self.send('print("Hello from remote tmux!")', pane=pane_id)
                time.sleep(0.5)
                
                # Capture output
                print("\nCaptured output:")
                self.capture(pane=pane_id, lines=10)
                
                # Clean up
                print("\nCleaning up...")
                self.send('exit()', pane=pane_id)
                time.sleep(0.5)
                self.kill(pane=pane_id)
                print("Demo complete!")
            else:
                print("Failed to wait for Python")
                self.kill(pane=pane_id)
    
    def help(self):
        """Display tmux-cli usage instructions."""
        # Show status first if in tmux
        if self.in_tmux:
            print("CURRENT TMUX STATUS:")
            print("=" * 60)
            self.status()
            print("=" * 60)
            print()
        
        # Add mode-specific header
        mode_info = f"TMUX-CLI HELP\n{'='*60}\n"
        if self.mode == 'local':
            mode_info += "MODE: LOCAL (inside tmux) - Managing panes in current window\n"
        else:
            mode_info += f"MODE: REMOTE (outside tmux) - Managing windows in session '{self.controller.session_name}'\n"
        mode_info += f"{'='*60}\n"
        
        print(mode_info)
        print(_load_help_text())
        
        if self.mode == 'remote':
            print("\n" + "="*60)
            print("REMOTE MODE SPECIFIC COMMANDS:")
            print("- tmux-cli attach: Attach to the managed session to view live")
            print("- tmux-cli cleanup: Kill the entire managed session")
            print("- tmux-cli list_windows: List all windows in the session")
            print("\nNote: In remote mode, 'panes' are actually windows for better isolation.")
            print("="*60)
        else:
            print("\n" + "="*60)
            print("LOCAL MODE PANE IDENTIFIERS:")
            print("- session:window.pane format (e.g., 'cc-tools:1.2')")
            print("- Pane IDs (e.g., '%12') for backwards compatibility")
            print("- Just pane index (e.g., '2') for current window")
            print("="*60)


def main():
    """Main entry point using fire."""
    import fire
    import sys
    
    # Check for --help flag
    if '--help' in sys.argv:
        cli = CLI()
        cli.help()
        sys.exit(0)
    
    fire.Fire(CLI)


if __name__ == '__main__':
    main()