"""Generate OpenCode configuration file (opencode.json)."""

import json
from pathlib import Path

from ..models import (
    ClaudeCodeConfig,
    ClaudeCodeMcpServer,
    MCP_TYPE_MAPPING,
)


def convert_mcp_server(server: ClaudeCodeMcpServer) -> dict:
    """
    Convert Claude Code MCP server config to OpenCode format.

    Args:
        server: ClaudeCodeMcpServer object.

    Returns:
        OpenCode MCP server config dict.
    """
    opencode_type = MCP_TYPE_MAPPING.get(server.server_type, "local")

    config: dict = {"type": opencode_type}

    if opencode_type == "local":
        # Combine command and args into a list
        if server.command:
            cmd_list = [server.command]
            if server.args:
                cmd_list.extend(server.args)
            config["command"] = cmd_list

        if server.env:
            config["environment"] = server.env

    else:  # remote
        if server.url:
            config["url"] = server.url
        if server.headers:
            config["headers"] = server.headers

    return config


def generate_opencode_config(
    claude_config: ClaudeCodeConfig,
    existing_config: dict | None = None,
) -> dict:
    """
    Generate OpenCode configuration from Claude Code config.

    Args:
        claude_config: ClaudeCodeConfig object.
        existing_config: Existing opencode.json content to merge with.

    Returns:
        OpenCode configuration dict.
    """
    config = existing_config.copy() if existing_config else {}

    # Add schema reference
    if "$schema" not in config:
        config["$schema"] = "https://opencode.ai/config.json"

    # Convert MCP servers
    if claude_config.mcp_servers:
        mcp_config = config.get("mcp", {})
        for server in claude_config.mcp_servers:
            mcp_config[server.name] = convert_mcp_server(server)
        config["mcp"] = mcp_config

    return config


def write_opencode_config(
    claude_config: ClaudeCodeConfig,
    output_dir: Path,
) -> Path:
    """
    Generate and write opencode.json file.

    Args:
        claude_config: ClaudeCodeConfig object.
        output_dir: Directory to write config to (.opencode/).

    Returns:
        Path to the generated config file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "opencode.json"

    # Load existing config if present
    existing_config = None
    if output_path.exists():
        try:
            with open(output_path) as f:
                existing_config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    config = generate_opencode_config(claude_config, existing_config)

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    return output_path
