#!/usr/bin/env python
"""
Demonstration of hide_transmission feature.

This script demonstrates the difference between visible and hidden transmission modes.

Run this in a tmux session to see the difference:
    python demo_hide_transmission.py

Watch your tmux pane to see the difference!
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from claude_code_tools.tmux_cli_controller import TmuxCLIController


def demo():
    """Demonstrate hide_transmission feature."""
    print("=" * 70)
    print("hide_transmission Feature Demonstration")
    print("=" * 70)
    print()
    print("Watch the tmux pane where this script is running!")
    print("You'll see the difference between visible and hidden modes.")
    print()

    ctrl = TmuxCLIController()

    # Get current pane
    import subprocess
    try:
        pane = subprocess.check_output(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            text=True
        ).strip()
        ctrl.target_pane = pane
    except:
        print("ERROR: Please run this script from within a tmux session.")
        return 1

    print("Demo 1: Visible transmission (default behavior)")
    print("-" * 70)
    print("The technical markers will be visible in the pane...")
    time.sleep(2)

    result = ctrl.execute("echo 'This is visible mode'", hide_transmission=False)
    print(f"Output: {result['output'].strip()}")
    print(f"Exit code: {result['exit_code']}")
    print()

    time.sleep(3)

    print("Demo 2: Hidden transmission (clean viewport)")
    print("-" * 70)
    print("The technical markers are hidden from view...")
    time.sleep(2)

    result = ctrl.execute("echo 'This is hidden mode'", hide_transmission=True)
    print(f"Output: {result['output'].strip()}")
    print(f"Exit code: {result['exit_code']}")
    print()

    print("=" * 70)
    print("Demo complete!")
    print()
    print("Notice how in visible mode, you saw:")
    print("  echo __TMUX_EXEC_START_xxx__; { echo ...; } 2>&1; echo __TMUX_EXEC_END_xxx__:$?")
    print()
    print("But in hidden mode, you only saw the clean output!")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(demo())
