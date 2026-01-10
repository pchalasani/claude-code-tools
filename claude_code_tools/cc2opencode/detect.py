"""Detect Claude Code configuration in a project directory."""

import json
from pathlib import Path

from .models import ClaudeCodeConfig
from .parsers import (
    parse_hooks,
    parse_agents,
    parse_commands,
    parse_mcp,
    parse_skills,
)


def detect_claude_code(path: Path) -> ClaudeCodeConfig:
    """
    Scan a directory for Claude Code configuration.

    Args:
        path: The project directory to scan. Can be a project directory
              (will look for .claude/ subdirectory) or the .claude directory
              itself (will look for agents/commands directly).

    Returns:
        ClaudeCodeConfig with all detected configurations.
    """
    path = Path(path).resolve()

    # Determine the claude_dir based on whether we're scanning .claude directly
    # or a project directory containing .claude
    if path.name == ".claude" or str(path).endswith("/.claude"):
        # We're scanning the .claude directory directly
        claude_dir = path
        project_dir = path.parent
    else:
        # We're scanning a project directory
        claude_dir = path / ".claude"
        project_dir = path

    config = ClaudeCodeConfig()

    # Parse hooks from settings.json
    settings_paths = [
        claude_dir / "settings.json",
        claude_dir / "settings.local.json",
        project_dir / ".claude.json",  # MCP config location
    ]
    for settings_path in settings_paths:
        if settings_path.exists():
            try:
                with open(settings_path) as f:
                    settings = json.load(f)
                if "hooks" in settings:
                    config.hooks.extend(parse_hooks(settings["hooks"]))
            except (json.JSONDecodeError, IOError):
                pass

    # Parse agents from agents/
    agents_dir = claude_dir / "agents"
    if agents_dir.exists():
        config.agents = parse_agents(agents_dir)

    # Parse commands from commands/
    commands_dir = claude_dir / "commands"
    if commands_dir.exists():
        config.commands = parse_commands(commands_dir)

    # Parse skills from skills/
    skills_dir = claude_dir / "skills"
    if skills_dir.exists():
        config.skills = parse_skills(skills_dir)

    # Parse MCP config
    mcp_paths = [
        project_dir / ".mcp.json",
        project_dir / ".claude.json",
        Path.home() / ".claude.json",
    ]
    for mcp_path in mcp_paths:
        if mcp_path.exists():
            servers = parse_mcp(mcp_path)
            if servers:
                config.mcp_servers.extend(servers)
                break  # Use first found

    # Read CLAUDE.md if present
    claude_md_path = project_dir / "CLAUDE.md"
    if claude_md_path.exists():
        try:
            config.claude_md = claude_md_path.read_text()
        except IOError:
            pass

    return config


def summarize_config(config: ClaudeCodeConfig) -> dict:
    """
    Generate a summary of detected configuration.

    Args:
        config: The detected ClaudeCodeConfig.

    Returns:
        Dictionary with counts and names of each component.
    """
    return {
        "hooks": {
            "count": len(config.hooks),
            "events": list(set(h.event for h in config.hooks)),
        },
        "agents": {
            "count": len(config.agents),
            "names": [a.name for a in config.agents],
        },
        "commands": {
            "count": len(config.commands),
            "names": [c.name for c in config.commands],
        },
        "skills": {
            "count": len(config.skills),
            "names": [s.name for s in config.skills],
        },
        "mcp_servers": {
            "count": len(config.mcp_servers),
            "names": [m.name for m in config.mcp_servers],
        },
        "has_claude_md": config.claude_md is not None,
    }
