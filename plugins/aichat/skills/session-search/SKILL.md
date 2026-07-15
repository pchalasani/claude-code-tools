---
name: session-search
description: For CLI agents WITHOUT subagent support (e.g., Codex CLI). Search previous code agent sessions for specific work, decisions, or code patterns.
---

> **If you are Claude Code:** Do NOT use this skill directly. Use the
> `session-searcher` subagent via the Task tool instead - it handles this more
> efficiently without polluting your context.

# session-search

Search and find previous code agent sessions (Claude-Code or Codex-CLI) for specific
work, decisions, or code patterns.

## Workflow

1. **Understand the query**: Identify what the user is looking for (code patterns,
   decisions, specific work, design direction)
2. **Search with aichat**: Run `aichat search --json -n 10 "[query]"` (use
   `-g "project"` to filter by project)
3. **Parse results**: Use `jq` to extract fields from JSONL output (session_id,
   project, created, snippet, file_path)
4. **Deep dive if needed**: Read session files at
   `~/.claude/projects/*/[session-id].jsonl` (max 3 files)
5. **Summarize**: Return a focused summary with key findings and references

Run `aichat search --help` to see all options (date filters, branch filters, etc.)
and JSONL field names.

## Output Format

Return a concise summary containing:

1. **Key Findings**: 2-3 bullet points answering the query
2. **Relevant Sessions**: Session IDs and dates for reference
3. **Specific Content**: Code snippets or quotes if directly relevant

Format as clean markdown, not raw JSON.

## Example

Query: "Find sessions where we discussed authentication design"

```bash
aichat search --json -n 10 "authentication design"
```

Summary:
- **Session abc123** (Dec 10): Discussed JWT vs session-based auth, decided on JWT
- **Session def456** (Dec 8): Implemented refresh token rotation pattern

## Constraints

- ALWAYS use `--json` flag with aichat search (otherwise it spawns interactive UI)
- NEVER return raw JSON output to the user - summarize and distill findings
- NEVER read more than 3 session files per query
- If no results found, suggest alternative search terms
- ONLY report information directly observed in files - never infer or extrapolate

## Error Handling

If `aichat search` command fails or is not found, ask user to install:

```bash
uv tool install claude-code-tools   # Python package
cargo install aichat-search         # Rust search TUI
```

Prerequisites:

- Node.js 18+ (for action menus)
- Rust/Cargo (for aichat-search)

If user doesn't have uv or cargo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh           # uv
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh  # Rust
```
