"""Generate OpenCode TypeScript plugins from Claude Code hooks."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader

from ..models import ClaudeCodeHook, HOOK_EVENT_MAPPING


def get_template_env() -> Environment:
    """Get Jinja2 environment with templates directory."""
    templates_dir = Path(__file__).parent.parent / "templates"
    return Environment(
        loader=FileSystemLoader(templates_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def matcher_to_condition(matcher: str | None) -> str:
    """
    Convert Claude Code matcher pattern to TypeScript condition.

    Args:
        matcher: Matcher pattern like "Edit|Write" or "Notebook.*".

    Returns:
        TypeScript condition string.
    """
    if not matcher or matcher == "*":
        return "true"

    # Handle pipe-separated list (Edit|Write)
    if "|" in matcher and ".*" not in matcher:
        tools = [t.strip() for t in matcher.split("|")]
        tools_json = ", ".join(f'"{t}"' for t in tools)
        return f"[{tools_json}].includes(input.tool)"

    # Handle regex patterns (Notebook.*, mcp__.*__write.*)
    # Convert to JavaScript regex
    regex_pattern = matcher.replace(".", "\\.").replace("*", ".*")
    return f'/{regex_pattern}/.test(input.tool)'


def translate_env_vars(command: str) -> str:
    """
    Translate Claude Code environment variables to OpenCode equivalents.

    Args:
        command: Bash command with Claude Code env vars.

    Returns:
        Command with OpenCode equivalents.
    """
    translations = {
        "$CLAUDE_FILE_PATHS": "${output.metadata?.filePath}",
        "${CLAUDE_FILE_PATHS}": "${output.metadata?.filePath}",
        "$CLAUDE_TOOL_NAME": "${input.tool}",
        "${CLAUDE_TOOL_NAME}": "${input.tool}",
        "$CLAUDE_SESSION_ID": "${input.sessionID}",
        "${CLAUDE_SESSION_ID}": "${input.sessionID}",
    }

    for old, new in translations.items():
        command = command.replace(old, new)

    return command


def generate_plugin(
    hooks: list[ClaudeCodeHook],
    plugin_name: str = "MigratedHooksPlugin",
) -> str:
    """
    Generate OpenCode TypeScript plugin from Claude Code hooks.

    Args:
        hooks: List of ClaudeCodeHook objects.
        plugin_name: Name for the generated plugin.

    Returns:
        TypeScript plugin source code.
    """
    env = get_template_env()
    template = env.get_template("plugin.ts.j2")

    # Group hooks by OpenCode event type
    hook_groups: dict[str, list[dict]] = {}

    for hook in hooks:
        opencode_event = HOOK_EVENT_MAPPING.get(hook.event)
        if not opencode_event:
            continue

        if opencode_event not in hook_groups:
            hook_groups[opencode_event] = []

        hook_groups[opencode_event].append({
            "original_event": hook.event,
            "matcher": hook.matcher,
            "condition": matcher_to_condition(hook.matcher),
            "hook_type": hook.hook_type,
            "command": translate_env_vars(hook.command) if hook.command else None,
            "prompt": hook.prompt,
            "timeout": hook.timeout,
        })

    return template.render(
        plugin_name=plugin_name,
        hook_groups=hook_groups,
    )


def write_plugin(
    hooks: list[ClaudeCodeHook],
    output_dir: Path,
    plugin_name: str = "migrated_hooks",
) -> Path:
    """
    Generate and write OpenCode plugin file.

    Args:
        hooks: List of ClaudeCodeHook objects.
        output_dir: Directory to write plugin to (.opencode/plugin/).
        plugin_name: Name for the plugin file (without extension).

    Returns:
        Path to the generated plugin file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{plugin_name}.ts"

    plugin_code = generate_plugin(hooks, plugin_name=f"{plugin_name.title()}Plugin")
    output_path.write_text(plugin_code)

    return output_path
