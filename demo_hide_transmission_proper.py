#!/usr/bin/env python
"""
Proper demonstration of hide_transmission feature.

This script:
1. Runs in your current pane
2. Creates a separate demo pane
3. Sends commands to that demo pane
4. You watch the demo pane to see the difference

Run from within tmux:
    python demo_hide_transmission_proper.py

Then watch the demo pane that appears!
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from claude_code_tools.tmux_cli_controller import TmuxCLIController


def demo():
    """Demonstrate hide_transmission by controlling a separate pane."""
    print("=" * 70)
    print("hide_transmission Feature Demonstration")
    print("=" * 70)
    print()

    ctrl = TmuxCLIController()

    # Create a separate demo pane
    print("Creating demo pane (watch the split!)...")
    demo_pane = ctrl.create_pane(vertical=False, size=50)
    if not demo_pane:
        print("ERROR: Could not create demo pane")
        return 1

    print(f"Demo pane created: {demo_pane}")
    print()
    time.sleep(2)

    # Clear the demo pane
    ctrl.send_keys("clear", pane_id=demo_pane, enter=True)
    time.sleep(1)

    print("=" * 70)
    print("WATCH THE DEMO PANE ABOVE!")
    print("=" * 70)
    print()

    # Demo 1: Visible transmission
    print("Sending command with VISIBLE transmission...")
    print("(You should see the ugly marker-wrapped command)")
    time.sleep(3)

    result = ctrl.execute("echo 'Visible mode test'", pane_id=demo_pane, hide_transmission=False)
    print(f"✓ Command completed (exit code: {result['exit_code']})")
    print()
    time.sleep(4)

    # Clear for next demo
    ctrl.send_keys("clear", pane_id=demo_pane, enter=True)
    time.sleep(2)

    # Demo 2: Hidden transmission
    print("Sending command with HIDDEN transmission...")
    print("(You should NOT see the marker-wrapped command, only clean output)")
    time.sleep(3)

    result = ctrl.execute("echo 'Hidden mode test'", pane_id=demo_pane, hide_transmission=True)
    print(f"✓ Command completed (exit code: {result['exit_code']})")
    print()
    time.sleep(4)

    print("=" * 70)
    print("Demo complete!")
    print()
    print("In visible mode, you saw:")
    print("  echo __TMUX_EXEC_START_xxx__; { echo ...; } 2>&1; echo __TMUX_EXEC_END_xxx__:$?")
    print()
    print("In hidden mode, you should have seen:")
    print("  [just the clean output without the wrapped command]")
    print()
    print("Press Enter to close the demo pane...")
    input()

    # Cleanup
    ctrl.kill_pane(demo_pane)
    print("Demo pane closed.")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(demo())
