#!/usr/bin/env python3
"""Shared utilities for Claude Code hooks."""
import json
import sys
from typing import Any


def load_and_validate_input() -> dict[str, Any]:
    """
    Load and validate JSON input from stdin.

    Returns:
        Validated dict from JSON input.

    Raises:
        SystemExit: If input is invalid, exits with error response.
    """
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({
            "decision": "block",
            "reason": "Invalid JSON input"
        }))
        sys.exit(1)
    except Exception:
        print(json.dumps({
            "decision": "block",
            "reason": "Failed to read input"
        }))
        sys.exit(1)

    # Validate data is a dictionary
    if not isinstance(data, dict):
        print(json.dumps({
            "decision": "block",
            "reason": "Invalid input format: expected object"
        }))
        sys.exit(1)

    return data


def approve() -> None:
    """Print approve decision and exit."""
    print(json.dumps({"decision": "approve"}))
    sys.exit(0)


def block(reason: str) -> None:
    """Print block decision with reason and exit."""
    print(json.dumps({
        "decision": "block",
        "reason": reason
    }, ensure_ascii=False))
    sys.exit(0)
