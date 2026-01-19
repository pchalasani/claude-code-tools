"""Helper functions for tmux command execution with exit code capture."""
import os
import re
import time
from typing import Tuple, Dict, Any, Callable, Optional, List


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


def find_markers_in_output(
    captured: str, start_marker: str, end_marker: str
) -> Dict[str, bool]:
    """Check if markers are present in captured output.

    Args:
        captured: Text captured from pane
        start_marker: Start marker to look for
        end_marker: End marker to look for

    Returns:
        Dict with keys: has_start (bool), has_end (bool)
    """
    return {
        "has_start": start_marker in captured,
        "has_end": f"{end_marker}:" in captured,
    }


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

    # Find the ECHOED start marker (on its own line), not the one in the typed command.
    # The echoed marker appears after a newline, while the typed command has it inline.
    # Search for "\n{start_marker}" to find the echoed version.
    newline_start_marker = "\n" + start_marker
    start_idx = captured_output.find(newline_start_marker)
    if start_idx != -1:
        # Found it after a newline - adjust index to point to the marker itself
        start_idx += 1  # Skip the newline
    else:
        # Fallback: marker might be at the very beginning (no preceding newline)
        if captured_output.startswith(start_marker):
            start_idx = 0
        else:
            # Last resort: use first occurrence (may include command text)
            start_idx = captured_output.find(start_marker)

    # Find the ECHOED end marker with exit code (e.g., "__END__:0")
    # The echoed end marker has a numeric exit code, while the typed command has "$?"
    # Search for end_marker followed by ":" and a digit
    end_pattern = re.escape(end_marker) + r":(\d+)"
    end_match = re.search(end_pattern, captured_output)

    if start_idx == -1 or end_match is None:
        return {
            "output": captured_output,
            "exit_code": -1
        }

    end_idx = end_match.start()
    exit_code = int(end_match.group(1))

    # Extract output between markers
    output_start = start_idx + len(start_marker)
    # Handle newline after start marker
    if output_start < len(captured_output) and captured_output[output_start] == '\n':
        output_start += 1

    output = captured_output[output_start:end_idx].rstrip('\n')

    return {
        "output": output,
        "exit_code": exit_code
    }


# Default expansion levels for progressive capture
EXPANSION_LEVELS: List[Optional[int]] = [100, 500, 2000, None]


def poll_for_completion(
    capture_fn: Callable[[Optional[int]], str],
    start_marker: str,
    end_marker: str,
    timeout: int = 30,
    expansion_levels: Optional[List[Optional[int]]] = None,
) -> Dict[str, Any]:
    """Poll for command completion with progressive expansion.

    Progressively expands capture size if end marker is found but start marker
    has scrolled off screen.

    Args:
        capture_fn: Function that captures pane output. Takes optional line count,
            returns captured text.
        start_marker: Start marker to look for
        end_marker: End marker to look for
        timeout: Maximum seconds to wait for completion (default: 30)
        expansion_levels: List of line counts to try (None = capture all).
            Defaults to [100, 500, 2000, None].

    Returns:
        Dict with keys:
            - output (str): Command output between markers
            - exit_code (int): Exit code from command, or -1 if markers not found
    """
    if expansion_levels is None:
        expansion_levels = EXPANSION_LEVELS

    start_time = time.time()
    while time.time() - start_time < timeout:
        # Try progressive expansion to find both markers
        for lines in expansion_levels:
            captured = capture_fn(lines)
            markers = find_markers_in_output(captured, start_marker, end_marker)

            if markers["has_end"]:
                if markers["has_start"]:
                    # Both markers found - parse and return
                    return parse_marked_output(captured, start_marker, end_marker)
                # End found but start missing - try more lines
                continue
            else:
                # End marker not found yet - command still running
                break

        time.sleep(0.5)

    # Timeout - capture with full expansion and return what we have
    captured = ""
    for lines in expansion_levels:
        captured = capture_fn(lines)
        markers = find_markers_in_output(captured, start_marker, end_marker)
        if markers["has_start"] or lines is None:
            break

    return parse_marked_output(captured, start_marker, end_marker)
