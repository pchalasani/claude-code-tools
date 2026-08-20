#!/usr/bin/env python3
"""
UserPromptSubmit hook to toggle git commit approval.

Triggers:
- '>allow-git': Allow commits for this session
- '>allow-git off': Restore approval prompts for this session
- '>allow-git status': Show current status

Commits are normally allowed everywhere by setting CCTOOLS_ALLOW_GIT=1 in the
environment. This hook is the per-session override: '>allow-git off' writes a
session-scoped deny flag that wins over the environment variable, and
'>allow-git' removes it (and writes a session-scoped allow flag, which is what
enables commits when the environment variable is not set).

Staging is no longer gated: 'git add' of specific paths never prompts, while
bulk staging ('git add -A', 'git add .', wildcards) stays blocked outright.
"""
import json
import os
import sys

TRIGGER = ">allow-git"
FLAG_DIR = "/tmp/claude"
ALLOW_FLAG = "allow-git-commit"
DENY_FLAG = "deny-git-commit"
ALLOW_ENV_VAR = "CCTOOLS_ALLOW_GIT"
_TRUTHY = {"1", "true", "yes", "on"}

# ANSI colors
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def _flag_path(name: str, session_id: str) -> str:
    return os.path.join(FLAG_DIR, f"{name}.{session_id}")


def _remove(name: str, session_id: str) -> None:
    try:
        os.remove(_flag_path(name, session_id))
    except FileNotFoundError:
        pass


def _env_allows() -> bool:
    return os.environ.get(ALLOW_ENV_VAR, "").strip().lower() in _TRUTHY


def _allow(session_id: str) -> str:
    """Clear any deny flag and record the session-scoped allowance."""
    os.makedirs(FLAG_DIR, exist_ok=True)
    _remove(DENY_FLAG, session_id)
    with open(_flag_path(ALLOW_FLAG, session_id), "w") as f:
        f.write(session_id)

    return (
        f"{GREEN}Git commits allowed for this session.{RESET}\n"
        f"{BLUE}Use >allow-git off to restore approval prompts.{RESET}"
    )


def _deny(session_id: str) -> str:
    """Write the deny flag, which also overrides the environment variable."""
    os.makedirs(FLAG_DIR, exist_ok=True)
    _remove(ALLOW_FLAG, session_id)
    with open(_flag_path(DENY_FLAG, session_id), "w") as f:
        f.write(session_id)

    return f"{YELLOW}Git commit approval prompts restored.{RESET}"


def _status(session_id: str) -> str:
    """Report whether commits currently need approval, and why."""
    if os.path.exists(_flag_path(DENY_FLAG, session_id)):
        return (
            f"{YELLOW}Commits require approval "
            f"(>allow-git off is set for this session).{RESET}\n"
            f"{BLUE}Use >allow-git to allow them again.{RESET}"
        )
    if _env_allows():
        return (
            f"{GREEN}Commits allowed ({ALLOW_ENV_VAR} is set).{RESET}\n"
            f"{BLUE}Use >allow-git off to restore prompts here.{RESET}"
        )
    if os.path.exists(_flag_path(ALLOW_FLAG, session_id)):
        return (
            f"{GREEN}Commits allowed for this session.{RESET}\n"
            f"{BLUE}Use >allow-git off to restore prompts.{RESET}"
        )
    return f"{BLUE}Commits require approval.{RESET}"


def main():
    try:
        data = json.load(sys.stdin)
        session_id = data.get("session_id", "")
        prompt = data.get("prompt")

        if not isinstance(prompt, str) or not prompt.strip():
            sys.exit(0)

        prompt = prompt.strip().lower()

        # Must match trigger exactly or as prefix + space
        if prompt != TRIGGER and not prompt.startswith(TRIGGER + " "):
            sys.exit(0)

        if not session_id:
            print(json.dumps({
                "decision": "block",
                "reason": "No session ID available.",
            }))
            sys.exit(0)

        # Parse the sub-command after ">allow-git"
        arg = prompt[len(TRIGGER):].strip()

        if arg == "off":
            message = _deny(session_id)
        elif arg == "status":
            message = _status(session_id)
        else:
            # No arg, or a legacy 'staging'/'commit' arg -> allow commits
            message = _allow(session_id)

        print(json.dumps({
            "decision": "block",
            "reason": message,
        }))
        sys.exit(0)

    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
