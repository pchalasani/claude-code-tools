#!/usr/bin/env python3
"""
Tmux CLI Controller for Claude Code
This script provides functions to interact with CLI applications running in tmux panes.
"""

import subprocess
import time
import re
from typing import Optional, List, Dict, Tuple
import json
import os


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
    
    def list_panes(self) -> List[Dict[str, str]]:
        """
        List all panes in the current window.
        
        Returns:
            List of dicts with pane info (id, index, title, active, size)
        """
        target = f"{self.session_name}:{self.window_name}" if self.session_name and self.window_name else ""
        
        output, code = self._run_tmux_command([
            'list-panes',
            '-t', target,
            '-F', '#{pane_id}|#{pane_index}|#{pane_title}|#{pane_active}|#{pane_width}x#{pane_height}'
        ] if target else [
            'list-panes',
            '-F', '#{pane_id}|#{pane_index}|#{pane_title}|#{pane_active}|#{pane_width}x#{pane_height}'
        ])
        
        if code != 0:
            return []
        
        panes = []
        for line in output.split('\n'):
            if line:
                parts = line.split('|')
                panes.append({
                    'id': parts[0],
                    'index': parts[1],
                    'title': parts[2],
                    'active': parts[3] == '1',
                    'size': parts[4]
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
        cmd = ['split-window']
        
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
    
    def send_keys(self, text: str, pane_id: Optional[str] = None, enter: bool = True):
        """
        Send keystrokes to a pane.
        
        Args:
            text: Text to send
            pane_id: Target pane (uses self.target_pane if not specified)
            enter: Whether to send Enter key after text
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
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
    
    def kill_pane(self, pane_id: Optional[str] = None):
        """
        Kill a pane.
        
        Args:
            pane_id: Target pane (uses self.target_pane if not specified)
        """
        target = pane_id or self.target_pane
        if not target:
            raise ValueError("No target pane specified")
        
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
            Pane ID of the created pane
        """
        return self.create_pane(vertical=vertical, size=size, start_command=command)


class CLI:
    """CLI interface for TmuxCLIController."""
    
    def __init__(self):
        self.controller = TmuxCLIController()
    
    def list_panes(self):
        """List all panes in current window."""
        panes = self.controller.list_panes()
        print(json.dumps(panes, indent=2))
    
    def launch(self, command: str, vertical: bool = True, size: int = 50):
        """Launch a command in a new pane."""
        pane_id = self.controller.launch_cli(command, vertical=vertical, size=size)
        print(f"Launched in pane: {pane_id}")
        return pane_id
    
    def send(self, text: str, pane: Optional[str] = None):
        """Send text to a pane."""
        if pane:
            if pane.isdigit():
                self.controller.select_pane(pane_index=int(pane))
            else:
                self.controller.select_pane(pane_id=pane)
        self.controller.send_keys(text)
        print("Text sent")
    
    def capture(self, pane: Optional[str] = None, lines: Optional[int] = None):
        """Capture and print pane content."""
        if pane:
            if pane.isdigit():
                self.controller.select_pane(pane_index=int(pane))
            else:
                self.controller.select_pane(pane_id=pane)
        content = self.controller.capture_pane(lines=lines)
        print(content)
        return content
    
    def interrupt(self, pane: Optional[str] = None):
        """Send Ctrl+C to a pane."""
        if pane:
            if pane.isdigit():
                self.controller.select_pane(pane_index=int(pane))
            else:
                self.controller.select_pane(pane_id=pane)
        self.controller.send_interrupt()
        print("Sent interrupt signal")
    
    def kill(self, pane: Optional[str] = None):
        """Kill a pane."""
        if pane:
            if pane.isdigit():
                self.controller.select_pane(pane_index=int(pane))
            else:
                self.controller.select_pane(pane_id=pane)
        self.controller.kill_pane()
        print("Pane killed")
    
    def demo(self):
        """Run a demo showing tmux CLI control capabilities."""
        print("Running demo...")
        
        # List current panes
        print("\nCurrent panes:")
        panes = self.controller.list_panes()
        for pane in panes:
            print(f"  Pane {pane['index']}: {pane['id']} - {pane['title']}")
        
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


def main():
    """Main entry point using fire."""
    import fire
    fire.Fire(CLI)


if __name__ == '__main__':
    main()