---
title: "Parallel Search MCP"
description: >
  Run free web searches and fetch page excerpts through an anonymous HTTP MCP client.
---

[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)
provides free web search and page fetching without an account or API key.
Anonymous search uses Fast mode and is intended for light use, with lower
rate limits than authenticated access.

The repository includes a standalone Python example that calls `web_search`
and `web_fetch` over Streamable HTTP at `https://search.parallel.ai/mcp`.
It prints the complete MCP result as JSON, including source URLs, excerpts,
warnings, and errors. No LLM or agent login is needed to run this example.

## Run the example

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then
clone this repository and run the script with Python 3.11 or newer:

```bash
git clone https://github.com/pchalasani/claude-code-tools
cd claude-code-tools
uv run --script demos/parallel_search.py --session-id research-example search \
  "Find the claude-code-tools installation instructions" \
  "claude-code-tools installation uv"
```

`uv run --script` installs the dependencies declared in the script in an
isolated environment. The example uses the MCP Python SDK 1.x; it is separate
from the installed `aichat` command.

Fetch excerpts from a source using the same session ID for related calls:

```bash
uv run --script demos/parallel_search.py --session-id research-example fetch \
  https://pchalasani.github.io/claude-code-tools/getting-started/ \
  --objective "Installation commands"
```

Each invocation opens and closes its own MCP connection. Omit `--session-id`
to generate a fresh UUID for independent research. Requests use a 60-second
deadline, send no authorization header, and do not read saved credentials.
Tool errors and page-fetch errors produce a nonzero exit code; inspect the
JSON for details. Network failures and timeouts also exit unsuccessfully.
If the service reports a rate limit, wait for its retry interval before
running another request.
