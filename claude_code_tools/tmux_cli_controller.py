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
    """Controller for interacting with CLI applications in tmux panes."""
    
    def __init__(self, session_name: Optional[str] = None, window_name: Optional[str] = None):
        """
        Initialize the controller.
        
        Args:
            session_name: Name of tmux session (defaults to current)
            window_name: Name of tmux window (defaults to current)
        """
        self.session_name = session_name
        self.window_name = window_name
        self.target_pane = None
    
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
    
    def format_pane_identifier(self, pane_id: str) -> str:
        """Convert pane ID to session:window.pane format."""
        try:
            # Get session, window index, and pane index for this pane
            session_output, session_code = self._run_tmux_command(['display-message', '-t', pane_id, '-p', '#{session_name}'])
            window_output, window_code = self._run_tmux_command(['display-message', '-t', pane_id, '-p', '#{window_index}'])
            pane_output, pane_code = self._run_tmux_command(['display-message', '-t', pane_id, '-p', '#{pane_index}'])
            
            if session_code == 0 and window_code == 0 and pane_code == 0:
                return f"{session_output}:{window_output}.{pane_output}"
            else:
                # Fallback to pane ID
                return pane_id
        except:
            return pane_id
    
    def resolve_pane_identifier(self, identifier: str) -> Optional[str]:
        """Convert various pane identifier formats to pane ID.
        
        Supports:
        - Pane IDs: %123
        - session:window.pane: mysession:1.2
        - Just pane index: 2 (for current window)
        """
        if not identifier:
            return None
        
        # Convert to string if it's a number
        identifier = str(identifier)
            
        # If it's already a pane ID (%123), return as is
        if identifier.startswith('%'):
            return identifier
            
        # If it's just a number, treat as pane index in current window
        if identifier.isdigit():
            panes = self.list_panes()
            for pane in panes:
                if pane['index'] == identifier:
                    return pane['id']
            return None
            
        # If it's session:window.pane format
        if ':' in identifier and '.' in identifier:
            try:
                session_window, pane_index = identifier.rsplit('.', 1)
                session, window = session_window.split(':', 1)
                
                # Get pane ID from session:window.pane
                output, code = self._run_tmux_command([
                    'display-message', '-t', f'{session}:{window}.{pane_index}', '-p', '#{pane_id}'
                ])
                return output if code == 0 else None
            except:
                return None
                
        return None
    
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
    
    def list_panes(self) -> List[Dict[str, str]]:
        """
        List all panes in the current window.

        Returns:
            List of dicts with pane info (id, index, title, active, size, command, formatted_id)
        """
        # Use explicit target if provided
        if self.session_name and self.window_name:
            target = f"{self.session_name}:{self.window_name}"
        else:
            # Use the window where the command is being executed from
            target = self.get_current_window_id()

        if target:
            output, code = self._run_tmux_command([
                'list-panes',
                '-t', target,
                '-F', '#{pane_id}|#{pane_index}|#{pane_title}|#{pane_active}|#{pane_width}x#{pane_height}|#{pane_current_command}'
            ])
        else:
            # Fallback to default behavior
            output, code = self._run_tmux_command([
                'list-panes',
                '-F', '#{pane_id}|#{pane_index}|#{pane_title}|#{pane_active}|#{pane_width}x#{pane_height}|#{pane_current_command}'
            ])
        
        if code != 0:
            return []
        
        panes = []
        for line in output.split('\n'):
            if line:
                parts = line.split('|')
                pane_id = parts[0]
                panes.append({
                    'id': pane_id,
                    'index': parts[1],
                    'title': parts[2],
                    'active': parts[3] == '1',
                    'size': parts[4],
                    'command': parts[5] if len(parts) > 5 else '',
                    'formatted_id': self.format_pane_identifier(pane_id)
                })
        return panes
    
    def create_pane(self, vertical: bool = True, size: Optional[int] = None, 
                   start_command: Optional[str] = None) -> Optional[str]:
        """
        Create a new pane in the current window.
        
        Args:
            vertical: If True, split vertically (side by side), else horizontally
            size: Size percentage for the new pane (e.g., 50 for 50%)
            start_command: Command to run in the new pane
            
        Returns:
            Pane ID of the created pane
        """
        # Get the current window ID to ensure pane is created in this window
        current_window_id = self.get_current_window_id()
        
        cmd = ['split-window']
        
        # Target the specific window where tmux-cli was called from
        if current_window_id:
            cmd.extend(['-t', current_window_id])
        
        if vertical:
            cmd.append('-h')
        else:
            cmd.append('-v')
        
        if size:
            cmd.extend(['-p', str(size)])
        
        cmd.extend(['-P', '-F', '#{pane_id}'])
        
        if start_command:
            cmd.append(start_command)
        
        output, code = self._run_tmux_command(cmd)
        
        if code == 0:
            self.target_pane = output
            return output
        return None
    
    def select_pane(self, pane_id: Optional[str] = None, pane_index: Optional[int] = None):
        """
        Select a pane as the target for operations.
        
        Args:
            pane_id: Pane ID (e.g., %0, %1)
            pane_index: Pane index (0-based)
        """
        if pane_id:
            self.target_pane = pane_id
        elif pane_index is not None:
            panes = self.list_panes()
            for pane in panes:
                if int(pane['index']) == pane_index:
                    self.target_pane = pane['id']
                    break
    
    def send_keys(self, text: str, pane_id: Optional[str] = None, enter: bool = True,
                  delay_enter: Union[bool, float] = True):
        """
        Send keystrokes to a pane.
        
        Args:
            text: Text to send
            pane_id: Target pane (uses self.target_pane if not specified)
            enter: Whether to send Enter key after text
            delay_enter: If True, use 1.0s delay; if float, use that delay in seconds (default: True)
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        if enter and delay_enter:
            # Send text without Enter first
            cmd = ['send-keys', '-t', target, text]
            self._run_tmux_command(cmd)
            
            # Determine delay duration
            if isinstance(delay_enter, bool):
                delay = 1.0  # Default delay
            else:
                delay = float(delay_enter)
            
            # Apply delay
            time.sleep(delay)
            
            # Then send just Enter
            cmd = ['send-keys', '-t', target, 'Enter']
            self._run_tmux_command(cmd)
        else:
            # Original behavior
            cmd = ['send-keys', '-t', target, text]
            if enter:
                cmd.append('Enter')
            self._run_tmux_command(cmd)
    
    def capture_pane(self, pane_id: Optional[str] = None, lines: Optional[int] = None) -> str:
        """
        Capture the contents of a pane.
        
        Args:
            pane_id: Target pane (uses self.target_pane if not specified)
            lines: Number of lines to capture from bottom (captures all if None)
            
        Returns:
            Captured text content
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        cmd = ['capture-pane', '-t', target, '-p']
        
        if lines:
            cmd.extend(['-S', f'-{lines}'])
        
        output, _ = self._run_tmux_command(cmd)
        return output
    
    def wait_for_prompt(self, prompt_pattern: str, pane_id: Optional[str] = None, 
                       timeout: int = 10, check_interval: float = 0.5) -> bool:
        """
        Wait for a specific prompt pattern to appear in the pane.
        
        Args:
            prompt_pattern: Regex pattern to match
            pane_id: Target pane
            timeout: Maximum seconds to wait
            check_interval: Seconds between checks
            
        Returns:
            True if prompt found, False if timeout
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        pattern = re.compile(prompt_pattern)
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            content = self.capture_pane(target, lines=50)
            if pattern.search(content):
                return True
            time.sleep(check_interval)
        
        return False
    
    def wait_for_idle(self, pane_id: Optional[str] = None, idle_time: float = 2.0,
                     check_interval: float = 0.5, timeout: Optional[int] = None) -> bool:
        """
        Wait for a pane to become idle (no output changes for idle_time seconds).
        
        Args:
            pane_id: Target pane
            idle_time: Seconds of no change to consider idle
            check_interval: Seconds between checks
            timeout: Maximum seconds to wait (None for no timeout)
            
        Returns:
            True if idle detected, False if timeout
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        start_time = time.time()
        last_change_time = time.time()
        last_hash = ""
        
        while True:
            if timeout and (time.time() - start_time > timeout):
                return False
                
            content = self.capture_pane(target)
            content_hash = hashlib.md5(content.encode()).hexdigest()
            
            if content_hash != last_hash:
                last_hash = content_hash
                last_change_time = time.time()
            elif time.time() - last_change_time >= idle_time:
                return True
                
            time.sleep(check_interval)
    
    def kill_pane(self, pane_id: Optional[str] = None):
        """
        Kill a pane.
        
        Args:
            pane_id: Target pane (uses self.target_pane if not specified)
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        # Safety check: prevent killing own pane ONLY when explicitly specified
        # If using target_pane (a pane we created), it should be safe to kill
        if pane_id is not None:  # Only check when pane_id was explicitly provided
            current_pane = self.get_current_pane()
            if current_pane and target == current_pane:
                raise ValueError("Error: Cannot kill own pane! This would terminate your session.")
        
        self._run_tmux_command(['kill-pane', '-t', target])
        
        if target == self.target_pane:
            self.target_pane = None
    
    def resize_pane(self, direction: str, amount: int = 5, pane_id: Optional[str] = None):
        """
        Resize a pane.
        
        Args:
            direction: One of 'up', 'down', 'left', 'right'
            amount: Number of cells to resize
            pane_id: Target pane
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        direction_map = {
            'up': '-U',
            'down': '-D',
            'left': '-L',
            'right': '-R'
        }
        
        if direction not in direction_map:
            raise ValueError(f"Invalid direction: {direction}")
        
        self._run_tmux_command(['resize-pane', '-t', target, direction_map[direction], str(amount)])
    
    def focus_pane(self, pane_id: Optional[str] = None):
        """
        Focus (select) a pane.
        
        Args:
            pane_id: Target pane
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        self._run_tmux_command(['select-pane', '-t', target])
    
    def send_interrupt(self, pane_id: Optional[str] = None):
        """
        Send Ctrl+C to a pane.
        
        Args:
            pane_id: Target pane
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        self._run_tmux_command(['send-keys', '-t', target, 'C-c'])
    
    def send_escape(self, pane_id: Optional[str] = None):
        """
        Send Escape key to a pane.
        
        Args:
            pane_id: Target pane
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        self._run_tmux_command(['send-keys', '-t', target, 'Escape'])
    
    def clear_pane(self, pane_id: Optional[str] = None):
        """
        Clear the pane screen.
        
        Args:
            pane_id: Target pane
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
        self._run_tmux_command(['send-keys', '-t', target, 'C-l'])
    
    def launch_cli(self, command: str, vertical: bool = True, size: int = 50) -> Optional[str]:
        """
        Convenience method to launch a CLI application in a new pane.
        
        Args:
            command: Command to launch
            vertical: Split direction
            size: Pane size percentage
            
        Returns:
            Formatted pane identifier (session:window.pane) of the created pane
        """
        pane_id = self.create_pane(vertical=vertical, size=size, start_command=command)
        if pane_id:
            return self.format_pane_identifier(pane_id)
        return None


class CLI:
    """Unified CLI interface that auto-detects tmux environment.
    
    Automatically uses:
    - TmuxCLIController when inside tmux (for pane management)
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
        """Show current tmux status and pane information."""
        if not self.in_tmux:
            print("Not currently in tmux")
            if hasattr(self.controller, 'session_name'):
                print(f"Remote session: {self.controller.session_name}")
            return
            
        # Get current location
        session = self.controller.get_current_session()
        window = self.controller.get_current_window()
        pane_index = self.controller.get_current_pane_index()
        
        if session and window and pane_index:
            print(f"Current location: {session}:{window}.{pane_index}")
        else:
            print("Could not determine current tmux location")
            
        # List all panes in current window
        panes = self.controller.list_panes()
        if panes:
            print(f"\nPanes in current window:")
            for pane in panes:
                active_marker = " *" if pane['active'] else "  "
                command = pane.get('command', '')
                title = pane.get('title', '')
                print(f"{active_marker} {pane['formatted_id']:15} {command:20} {title}")
        else:
            print("\nNo panes found")
    
    def list_panes(self):
        """List all panes in current window."""
        panes = self.controller.list_panes()
        print(json.dumps(panes, indent=2))
    
    def launch(self, command: str, vertical: bool = True, size: int = 50, name: Optional[str] = None):
        """Launch a command in a new pane/window.
        
        Args:
            command: Command to launch
            vertical: Split direction (only used in local mode)
            size: Pane size percentage (only used in local mode)
            name: Window name (only used in remote mode)
        """
        if self.mode == 'local':
            pane_id = self.controller.launch_cli(command, vertical=vertical, size=size)
            print(f"Launched '{command}' in pane {pane_id}")
        else:
            # Remote mode
            pane_id = self.controller.launch_cli(command, name=name)
            print(f"Launched '{command}' in window: {pane_id}")
        return pane_id
    
    def send(self, text: str, pane: Optional[str] = None, enter: bool = True,
             delay_enter: Union[bool, float] = True):
        """Send text to a pane.
        
        Args:
            text: Text to send
            pane: Target pane (session:window.pane, %id, or just index)
            enter: Whether to send Enter key after text
            delay_enter: If True, use 1.0s delay; if float, use that delay in seconds (default: True)
        """
        if self.mode == 'local':
            # Local mode - resolve pane identifier
            if pane:
                resolved_pane = self.controller.resolve_pane_identifier(pane)
                if resolved_pane:
                    self.controller.select_pane(pane_id=resolved_pane)
                else:
                    print(f"Could not resolve pane identifier: {pane}")
                    return
            self.controller.send_keys(text, enter=enter, delay_enter=delay_enter)
        else:
            # Remote mode - pass pane_id directly
            self.controller.send_keys(text, pane_id=pane, enter=enter,
                                    delay_enter=delay_enter)
        print("Text sent")
    
    def capture(self, pane: Optional[str] = None, lines: Optional[int] = None):
        """Capture and print pane content."""
        if self.mode == 'local':
            # Local mode - resolve pane identifier
            if pane:
                resolved_pane = self.controller.resolve_pane_identifier(pane)
                if resolved_pane:
                    self.controller.select_pane(pane_id=resolved_pane)
                else:
                    print(f"Could not resolve pane identifier: {pane}")
                    return ""
            content = self.controller.capture_pane(lines=lines)
        else:
            # Remote mode - pass pane_id directly
            content = self.controller.capture_pane(pane_id=pane, lines=lines)
        return content
    
    def interrupt(self, pane: Optional[str] = None):
        """Send Ctrl+C to a pane."""
        if self.mode == 'local':
            # Local mode - resolve pane identifier
            if pane:
                resolved_pane = self.controller.resolve_pane_identifier(pane)
                if resolved_pane:
                    self.controller.select_pane(pane_id=resolved_pane)
                else:
                    print(f"Could not resolve pane identifier: {pane}")
                    return
            self.controller.send_interrupt()
        else:
            # Remote mode - resolve and pass pane_id
            target = self.controller._resolve_pane_id(pane)
            self.controller.send_interrupt(pane_id=target)
        print("Sent interrupt signal")
    
    def escape(self, pane: Optional[str] = None):
        """Send Escape key to a pane."""
        if self.mode == 'local':
            # Local mode - resolve pane identifier
            if pane:
                resolved_pane = self.controller.resolve_pane_identifier(pane)
                if resolved_pane:
                    self.controller.select_pane(pane_id=resolved_pane)
                else:
                    print(f"Could not resolve pane identifier: {pane}")
                    return
            self.controller.send_escape()
        else:
            # Remote mode - resolve and pass pane_id
            target = self.controller._resolve_pane_id(pane)
            self.controller.send_escape(pane_id=target)
        print("Sent escape key")
    
    def kill(self, pane: Optional[str] = None):
        """Kill a pane/window."""
        if self.mode == 'local':
            # Local mode - kill pane
            if pane:
                resolved_pane = self.controller.resolve_pane_identifier(pane)
                if resolved_pane:
                    self.controller.select_pane(pane_id=resolved_pane)
                else:
                    print(f"Could not resolve pane identifier: {pane}")
                    return
            try:
                self.controller.kill_pane()
                print("Pane killed")
            except ValueError as e:
                print(str(e))
        else:
            # Remote mode - kill window
            try:
                self.controller.kill_window(window_id=pane)
                print("Window killed")
            except ValueError as e:
                print(str(e))
    
    def wait_idle(self, pane: Optional[str] = None, idle_time: float = 2.0, 
                  timeout: Optional[int] = None):
        """Wait for pane to become idle (no output changes)."""
        if self.mode == 'local':
            # Local mode - resolve pane identifier
            if pane:
                resolved_pane = self.controller.resolve_pane_identifier(pane)
                if resolved_pane:
                    self.controller.select_pane(pane_id=resolved_pane)
                else:
                    print(f"Could not resolve pane identifier: {pane}")
                    return False
            target = None
        else:
            # Remote mode - resolve pane_id
            target = self.controller._resolve_pane_id(pane)
        
        print(f"Waiting for pane to become idle (no changes for {idle_time}s)...")
        if self.controller.wait_for_idle(pane_id=target, idle_time=idle_time, timeout=timeout):
            print("Pane is idle")
            return True
        else:
            print("Timeout waiting for idle")
            return False
    
    def attach(self):
        """Attach to the managed session (remote mode only)."""
        if self.mode == 'local':
            print("Attach is only available in remote mode (when outside tmux)")
            return
        self.controller.attach_session()
    
    def cleanup(self):
        """Kill the entire managed session (remote mode only)."""
        if self.mode == 'local':
            print("Cleanup is only available in remote mode (when outside tmux)")
            return
        self.controller.cleanup_session()
    
    def list_windows(self):
        """List all windows in the session (remote mode only)."""
        if self.mode == 'local':
            print("List_windows is only available in remote mode. Use list_panes instead.")
            return
        
        windows = self.controller.list_windows()
        if not windows:
            print(f"No windows in session '{self.controller.session_name}'")
            return
        
        print(f"Windows in session '{self.controller.session_name}':")
        for w in windows:
            active = " (active)" if w['active'] else ""
            print(f"  {w['index']}: {w['name']}{active} - pane {w['pane_id']}")
    
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
