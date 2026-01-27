#!/usr/bin/env python3
"""
Example demonstrating the clean execution interface pattern.

This script shows how the execute_clean() method provides a clean
interface compared to the standard execute() method.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'claude_code_tools'))

from tmux_cli_controller import TmuxCLIController

def demo_clean_execution():
    """Demonstrate clean execution interface."""
    
    print("🎉 Clean Execution Interface Demonstration")
    print("=" * 50)
    
    # Initialize controller (assuming target_pane is set)
    controller = TmuxCLIController()
    
    # Example 1: Simple command
    print("1. Running: echo 'Hello from clean execution!'")
    result = controller.execute_clean("echo 'Hello from clean execution!'")
    print(f"   Output: {result['output']}")
    print(f"   Exit code: {result['exit_code']}")
    print()
    
    # Example 2: Current directory  
    print("2. Running: pwd")
    result = controller.execute_clean("pwd")
    print(f"   Output: {result['output']}")
    print(f"   Duration: {result.get('duration_ms', 'N/A')}ms")
    print()
    
    # Example 3: Current date (demonstrates it's working properly)
    print("3. Running: date +%Y-%m-%d")
    result = controller.execute_clean("date +%Y-%m-%d")
    print(f"   Output: {result['output']}")
    print()
    
    # Example 4: Complex command with pipes
    print("4. Running: find . -name '*.py' | wc -l")
    result = controller.execute_clean("find . -name '*.py' | wc -l")
    print(f"   Python files count: {result['output']}")
    print()
    
    print("🎯 Key Benefits:")
    print("   ✅ No technical markers visible")
    print("   ✅ Clean command output")
    print("   ✅ Structured JSON response")
    print("   ✅ History cleared (no contamination)")
    print("   ✅ Uses existing robust marker system")

def show_integration_with_existing():
    """Show how this builds on existing functionality."""
    
    print("\n🔧 Integration Example")
    print("=" * 30)
    print()
    print("The execute_clean() method builds on the existing:")
    print("• tmux_execution_helpers.py marker system")
    print("• __TMUX_EXEC_START_*/END_* architecture")  
    print("• poll_for_completion() sophisticated logic")
    print("• wrap_command_with_markers() proven approach")
    print()
    print("Enhanced with:")
    print("• stty-based echo hiding")
    print("• History clearing for cleanliness")
    print("• Clean output parsing")
    print("• User-friendly interface layer")

if __name__ == "__main__":
    demo_clean_execution()
    show_integration_with_existing()