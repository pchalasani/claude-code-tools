"""Parse Claude Code commands from .claude/commands/*.md."""

from pathlib import Path

import frontmatter

from ..models import ClaudeCodeCommand


def parse_command(path: Path, base_dir: Path | None = None) -> ClaudeCodeCommand:
    """
    Parse a single Claude Code command file.

    Args:
        path: Path to the command markdown file.
        base_dir: Base commands directory for namespace calculation.

    Returns:
        ClaudeCodeCommand object.
    """
    post = frontmatter.load(path)

    # Calculate name with namespace if in subdirectory
    if base_dir:
        rel_path = path.relative_to(base_dir)
        # Convert path/to/command.md to path:to:command
        parts = list(rel_path.parts)
        parts[-1] = parts[-1].replace(".md", "")
        name = ":".join(parts)
    else:
        name = path.stem

    return ClaudeCodeCommand(
        name=name,
        description=post.get("description"),
        allowed_tools=post.get("allowed-tools"),
        argument_hint=post.get("argument-hint"),
        model=post.get("model"),
        template=post.content,
    )


def parse_commands(commands_dir: Path) -> list[ClaudeCodeCommand]:
    """
    Parse all command files in a directory (including subdirectories).

    Args:
        commands_dir: Path to .claude/commands/ directory.

    Returns:
        List of ClaudeCodeCommand objects.
    """
    commands = []

    if not commands_dir.exists():
        return commands

    # Recursively find all .md files
    for command_file in commands_dir.rglob("*.md"):
        try:
            command = parse_command(command_file, base_dir=commands_dir)
            commands.append(command)
        except Exception as e:
            # Log warning but continue
            print(f"Warning: Failed to parse command {command_file}: {e}")

    return commands
