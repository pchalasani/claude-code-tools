"""Helper functions for tmux command execution with exit code capture."""
import os
import time
from typing import Tuple, Dict, Any


def generate_execution_markers() -> Tuple[str, str]:
    """Generate unique start and end markers for command execution.

    Uses PID and nanosecond timestamp to ensure uniqueness across
    concurrent calls.

    Returns:
        Tuple of (start_marker, end_marker)
    """
    timestamp = time.time_ns()
    pid = os.getpid()
    unique_id = f"{pid}_{timestamp}"

    start_marker = f"__TMUX_EXEC_START_{unique_id}__"
    end_marker = f"__TMUX_EXEC_END_{unique_id}__"

    return start_marker, end_marker


def wrap_command_with_markers(command: str, start_marker: str, end_marker: str) -> str:
    """Wrap command with markers to capture exit code.

    The wrapped command structure:
    1. Echo start marker
    2. Execute command in subshell, capture all output
    3. Echo end marker with exit code

    Args:
        command: Shell command to wrap
        start_marker: Marker to echo before command
        end_marker: Marker to echo after command with exit code

    Returns:
        Wrapped command string ready to send to shell
    """
    wrapped = f'echo {start_marker}; {{ {command}; }} 2>&1; echo {end_marker}:$?'
    return wrapped


def parse_marked_output(captured_output: str, start_marker: str, end_marker: str) -> Dict[str, Any]:
    """Parse marked output to extract command output and exit code.

    Args:
        captured_output: Text captured from pane
        start_marker: Start marker to look for
        end_marker: End marker to look for (with :exit_code suffix)

    Returns:
        Dict with keys:
            - output (str): Command output between markers
            - exit_code (int): Exit code from command, or -1 if markers not found
    """
    # Look for markers in output
    if start_marker not in captured_output or end_marker not in captured_output:
        # Markers not found - likely timeout
        return {
            "output": captured_output,
            "exit_code": -1
        }

    # Find marker positions
    start_idx = captured_output.find(start_marker)
    end_search = end_marker + ":"

    # Find last occurrence of end marker (in case output contains marker-like strings)
    end_idx = captured_output.rfind(end_search)

    if start_idx == -1 or end_idx == -1:
        return {
            "output": captured_output,
            "exit_code": -1
        }

    # Extract output between markers
    output_start = start_idx + len(start_marker)
    # Handle newline after start marker
    if output_start < len(captured_output) and captured_output[output_start] == '\n':
        output_start += 1

    output = captured_output[output_start:end_idx].rstrip('\n')

    # Extract exit code from end marker line
    end_marker_line = captured_output[end_idx:].split('\n')[0]
    exit_code_str = end_marker_line.split(':')[-1]

    try:
        exit_code = int(exit_code_str)
    except ValueError:
        exit_code = -1

    return {
        "output": output,
        "exit_code": exit_code
    }
