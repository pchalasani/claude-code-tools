#!/usr/bin/env python3
"""
Hook to handle session-related triggers in Claude Code.

Triggers (matched by hooks.json):
- '>resume', '>continue', '>handoff': Copy session ID + show resume instructions
- '>session', '>session-id': Copy session ID + show simple confirmation
"""
import json
import subprocess
import sys


def copy_to_clipboard(text: str) -> bool:
    """
    Copy text to clipboard. Tries multiple commands for cross-platform support.
    Returns True if successful, False otherwise.
    """
    # Commands to try in order (first one that works wins)
    clipboard_commands = [
        ["pbcopy"],  # macOS
        ["xclip", "-selection", "clipboard"],  # Linux X11
        ["xsel", "--clipboard", "--input"],  # Linux X11 alternative
        ["wl-copy"],  # Linux Wayland
        ["clip"],  # Windows
    ]

    for cmd in clipboard_commands:
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode(),
                capture_output=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return True
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue

    return False


def copy_session_id_and_format_message(
    session_id: str,
    show_resume_instructions: bool = False,
) -> str:
    """
    Copy session ID to clipboard and return a formatted message.

    Args:
        session_id: The session ID to copy.
        show_resume_instructions: If True, show full resume instructions.
            If False, show simple confirmation.

    Returns:
        Formatted message string with ANSI colors.
    """
    copied = copy_to_clipboard(session_id)

    # ANSI escape codes for bright blue color and code style
    BLUE = "\033[94m"
    CODE = "\033[37m"  # Regular white for code-like appearance
    RESET = "\033[0m"

    if show_resume_instructions:
        # Full resume workflow message
        if copied:
            return (
                f"{BLUE}Session ID copied to clipboard!{RESET}\n\n"
                f"{BLUE}To continue your work in a new session:{RESET}\n"
                f"{BLUE}  1. Quit Claude (Ctrl+D twice){RESET}\n"
                f"{BLUE}  2. Run: {CODE}`aichat resume <paste>`{RESET}\n\n"
                f"{BLUE}You can then choose between a few different ways of{RESET}\n"
                f"{BLUE}continuing your work.{RESET}\n\n"
                f"{BLUE}Session ID: {session_id}{RESET}"
            )
        else:
            return (
                f"{BLUE}Could not copy to clipboard. Here's your session ID:{RESET}\n\n"
                f"{BLUE}  {session_id}{RESET}\n\n"
                f"{BLUE}To continue your work in a new session:{RESET}\n"
                f"{BLUE}  1. Copy the session ID above{RESET}\n"
                f"{BLUE}  2. Quit Claude (Ctrl+D twice){RESET}\n"
                f"{BLUE}  3. Run: {CODE}`aichat resume <session-id>`{RESET}\n\n"
                f"{BLUE}You can then choose between a few different ways of{RESET}\n"
                f"{BLUE}continuing your work.{RESET}"
            )
    else:
        # Simple confirmation message
        if copied:
            return (
                f"{BLUE}Session ID copied to clipboard!{RESET}\n\n"
                f"{BLUE}Session ID: {session_id}{RESET}"
            )
        else:
            return (
                f"{BLUE}Could not copy to clipboard. Here's your session ID:{RESET}\n\n"
                f"{BLUE}  {session_id}{RESET}"
            )


def main():
    data = json.load(sys.stdin)
    session_id = data.get("session_id", "")
    prompt = data.get("prompt", "").strip().lower()

    # Matcher in hooks.json guarantees we only get valid triggers
    # Just need to distinguish: >session* = simple, everything else = full resume
    is_session_trigger = prompt.startswith(">session")

    if not session_id:
        # No session ID available
        result = {
            "decision": "block",
            "reason": "No session ID available.",
        }
        print(json.dumps(result))
        sys.exit(0)

    # Copy session ID and get formatted message
    message = copy_session_id_and_format_message(
        session_id,
        show_resume_instructions=not is_session_trigger,
    )

    # Block the prompt and show the message
    result = {
        "decision": "block",
        "reason": message,
    }
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
