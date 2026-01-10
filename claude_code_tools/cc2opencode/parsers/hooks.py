"""Parse Claude Code hooks from settings.json."""

from ..models import ClaudeCodeHook


def parse_hooks(hooks_config: dict) -> list[ClaudeCodeHook]:
    """
    Parse hooks configuration from Claude Code settings.json.

    Args:
        hooks_config: The "hooks" section from settings.json.

    Returns:
        List of ClaudeCodeHook objects.

    Example input:
        {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {"type": "command", "command": "prettier --write ..."}
                    ]
                }
            ]
        }
    """
    parsed_hooks = []

    for event_name, event_configs in hooks_config.items():
        for event_config in event_configs:
            matcher = event_config.get("matcher")
            hooks_list = event_config.get("hooks", [])

            for hook in hooks_list:
                hook_type = hook.get("type", "command")
                parsed_hooks.append(
                    ClaudeCodeHook(
                        event=event_name,
                        matcher=matcher,
                        hook_type=hook_type,
                        command=hook.get("command"),
                        prompt=hook.get("prompt"),
                        timeout=hook.get("timeout", 60),
                    )
                )

    return parsed_hooks
