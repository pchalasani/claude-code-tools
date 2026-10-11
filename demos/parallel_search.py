# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.30,<2", "httpx>=0.28,<1"]
# ///
"""Search or fetch web pages anonymously through Parallel's HTTP MCP server."""

import argparse
import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = "https://search.parallel.ai/mcp"
USER_AGENT = "claude-code-tools/parallel-search-example"


async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Initialize an anonymous MCP session and return the complete tool result.

    Args:
        name: MCP tool to execute.
        arguments: Arguments matching that tool's input schema.

    Returns:
        The MCP result, including structured content and any tool error.
    """
    async with asyncio.timeout(60):
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=60
        ) as client:
            async with streamable_http_client(
                MCP_URL, http_client=client
            ) as (read, write, _):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=60)
                ) as session:
                    await session.initialize()
                    return (await session.call_tool(name, arguments)).model_dump(
                        mode="json", exclude_none=True
                    )


def main() -> int:
    """Parse a search or fetch request, print JSON, and report tool errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session-id",
        default=str(uuid.uuid4()),
        help="Reuse this ID for related search and fetch calls (default: new UUID).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search for current web sources.")
    search.add_argument("objective", help="Describe the information you need.")
    search.add_argument("queries", nargs="+", help="One or more short search queries.")
    fetch = commands.add_parser("fetch", help="Extract excerpts from web pages.")
    fetch.add_argument("urls", nargs="+", help="HTTP/HTTPS URLs to fetch (up to 20).")
    fetch.add_argument("--objective", help="Focus excerpts on this topic.")
    args = parser.parse_args()
    arguments: dict[str, Any] = {"session_id": args.session_id}
    if args.command == "search":
        name = "web_search"
        arguments.update(objective=args.objective, search_queries=args.queries)
    else:
        name = "web_fetch"
        arguments["urls"] = args.urls
        if args.objective:
            arguments["objective"] = args.objective
    result = asyncio.run(call_tool(name, arguments))
    print(json.dumps(result, indent=2))
    return int(
        bool(
            result.get("isError")
            or (result.get("structuredContent") or {}).get("errors")
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
