#!/usr/bin/env python3
"""
Hook that logs TodoWrite operations and suggests sequential thinking for complex todos.
This hook does not block but provides guidance.
"""
import json
import os
from datetime import datetime

from hook_utils import load_and_validate_input, approve

def analyze_todos(todos):
    """Analyze todos and provide recommendations."""
    if not todos:
        return None

    # Count pending todos
    pending = [t for t in todos if t.get('status') == 'pending']

    # Check for complexity indicators
    complex_keywords = [
        'implement', 'refactor', 'migrate', 'integrate',
        'design', 'architect', 'optimize', 'security'
    ]

    complex_todos = []
    for todo in pending:
        content = todo.get('content', '').lower()
        if any(kw in content for kw in complex_keywords):
            complex_todos.append(todo.get('content'))

    if len(pending) > 5 or complex_todos:
        return {
            "suggestion": "Consider using sequential thinking for complex task breakdown",
            "complex_tasks": complex_todos[:3],  # Top 3
            "recommendation": "Use /smart-plan command or task-planner agent for better planning"
        }

    return None

def main():
    data = load_and_validate_input()

    tool_name = data.get("tool_name")
    if tool_name != "TodoWrite":
        approve()

    todos = data.get("tool_input", {}).get("todos", [])

    analysis = analyze_todos(todos)

    if analysis:
        # Log the suggestion (doesn't block, just informs)
        log_file = os.path.expanduser("~/.claude/logs/todo_suggestions.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        with open(log_file, 'a') as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "analysis": analysis,
                "todo_count": len(todos)
            }) + "\n")

    # Always approve - this hook is advisory only
    approve()

if __name__ == "__main__":
    main()
