"""Generate OpenCode command markdown from Claude Code commands."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..models import ClaudeCodeCommand, OpenCodeCommand


def get_template_env() -> Environment:
    """Get Jinja2 environment with templates directory."""
    templates_dir = Path(__file__).parent.parent / "templates"
    return Environment(
        loader=FileSystemLoader(templates_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def convert_command(command: ClaudeCodeCommand) -> OpenCodeCommand:
    """
    Convert Claude Code command to OpenCode command format.

    Args:
        command: ClaudeCodeCommand object.

    Returns:
        OpenCodeCommand object.
    """
    # Build description including argument hint if present
    description = command.description or ""
    if command.argument_hint:
        if description:
            description = f"{description} {command.argument_hint}"
        else:
            description = command.argument_hint

    # Template placeholders are mostly compatible
    # $ARGUMENTS, $1, $2, @filepath, !`cmd` all work the same
    template = command.template

    # Add note about allowed-tools if present (not supported in OpenCode)
    if command.allowed_tools:
        template = (
            f"<!-- Note: This command originally had allowed-tools: "
            f"{command.allowed_tools}. "
            f"OpenCode does not support tool restrictions per command. -->\n\n"
            f"{template}"
        )

    return OpenCodeCommand(
        name=command.name,
        description=description,
        model=command.model,
        template=template,
    )


def generate_command(command: ClaudeCodeCommand) -> str:
    """
    Generate OpenCode command markdown from Claude Code command.

    Args:
        command: ClaudeCodeCommand object.

    Returns:
        OpenCode command markdown content.
    """
    env = get_template_env()
    template = env.get_template("command.md.j2")

    opencode_command = convert_command(command)

    return template.render(command=opencode_command)


def write_command(command: ClaudeCodeCommand, output_dir: Path) -> Path:
    """
    Generate and write OpenCode command file.

    Args:
        command: ClaudeCodeCommand object.
        output_dir: Directory to write command to (.opencode/command/).

    Returns:
        Path to the generated command file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Handle namespaced commands (path:to:command -> path/to/command.md)
    if ":" in command.name:
        parts = command.name.split(":")
        subdir = output_dir / "/".join(parts[:-1])
        subdir.mkdir(parents=True, exist_ok=True)
        output_path = subdir / f"{parts[-1]}.md"
    else:
        output_path = output_dir / f"{command.name}.md"

    command_md = generate_command(command)
    output_path.write_text(command_md)

    return output_path
