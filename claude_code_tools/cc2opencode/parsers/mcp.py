"""Parse Claude Code MCP configuration."""

import json
from pathlib import Path

from ..models import ClaudeCodeMcpServer


def parse_mcp(mcp_path: Path) -> list[ClaudeCodeMcpServer]:
    """
    Parse MCP server configuration from a JSON file.

    Args:
        mcp_path: Path to .mcp.json or .claude.json.

    Returns:
        List of ClaudeCodeMcpServer objects.
    """
    servers = []

    if not mcp_path.exists():
        return servers

    try:
        with open(mcp_path) as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError):
        return servers

    # MCP servers can be under "mcpServers" key
    mcp_servers = config.get("mcpServers", {})

    for name, server_config in mcp_servers.items():
        server_type = server_config.get("type", "stdio")

        servers.append(
            ClaudeCodeMcpServer(
                name=name,
                server_type=server_type,
                command=server_config.get("command"),
                args=server_config.get("args"),
                url=server_config.get("url"),
                env=server_config.get("env"),
                headers=server_config.get("headers"),
            )
        )

    return servers
