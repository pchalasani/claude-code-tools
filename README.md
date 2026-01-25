# claude-code-tools

[![claude-code-tools](https://img.shields.io/github/v/release/pchalasani/claude-code-tools?filter=v*&label=claude-code-tools&color=blue)](https://pypi.org/project/claude-code-tools/)
[![aichat-search](https://img.shields.io/github/v/release/pchalasani/claude-code-tools?filter=rust-v*&label=aichat-search&color=orange)](https://github.com/pchalasani/claude-code-tools/releases?q=rust)

Productivity tools for Claude Code, Codex-CLI, and similar CLI coding agents:
CLI commands, skills, agents, hooks, plugins. Click on a card below to navigate.


<div align="center">

<table>
<tr>
<td align="center">
<a href="#quick-start">
<img src="assets/card-quickstart.svg" alt="quick start" width="300"/>
</a>
</td>
<td align="center">
<a href="#claude-code-plugins">
<img src="assets/card-plugins.svg" alt="plugins" width="300"/>
</a>
</td>
</tr>
</table>

<table>
<tr>
<td align="center">
<a href="#aichat-session-management">
<img src="assets/card-aichat.svg" alt="aichat" width="200"/>
</a>
</td>
<td align="center">
<a href="#tmux-cli-terminal-automation">
<img src="assets/card-tmux.svg" alt="tmux-cli" width="200"/>
</a>
</td>
<td align="center">
<a href="#lmsh-experimental">
<img src="assets/card-lmsh.svg" alt="lmsh" width="200"/>
</a>
</td>
</tr>
<tr>
<td align="center">
<a href="#vault">
<img src="assets/card-vault.svg" alt="vault" width="200"/>
</a>
</td>
<td align="center">
<a href="#env-safe">
<img src="assets/card-env-safe.svg" alt="env-safe" width="200"/>
</a>
</td>
<td align="center">
<a href="#claude-code-safety-hooks">
<img src="assets/card-safety.svg" alt="safety" width="200"/>
</a>
</td>
</tr>
<tr>
<td align="center">
<a href="#status-line">
<img src="assets/card-statusline.svg" alt="statusline" width="200"/>
</a>
</td>
<td align="center">
<a href="#google-docs-tools">
<img src="assets/card-gdocs.svg" alt="gdocs" width="200"/>
</a>
</td>
<td align="center">
<a href="#using-claude-code-with-open-weight-anthropic-api-compatible-llm-providers">
<img src="assets/card-alt.svg" alt="alt" width="200"/>
</a>
</td>
</tr>
<tr>
<td align="center">
<a href="#voice">
<img src="assets/card-voice.svg" alt="voice" width="200"/>
</a>
</td>
<td align="center">
</td>
<td align="center">
</td>
</tr>
</table>

<table>
<tr>
<td align="center">
<a href="#development">
<img src="assets/card-dev.svg" alt="development" width="300"/>
</a>
</td>
<td align="center">
<a href="LICENSE">
<img src="assets/card-license.svg" alt="license" width="300"/>
</a>
</td>
</tr>
</table>

</div>

---

<a id="quick-start"></a>
## 🚀 Quick Start

**Prerequisites:** Node.js 16+ (required for action menus)

**Step 1:** Install the Python package (includes Node.js UI components):
```bash
uv tool install claude-code-tools
```

**Step 2:** Install the Rust-based search engine (powers both human TUI and agent search).
Choose **one** of these methods:

- **Homebrew** (macOS/Linux): `brew install pchalasani/tap/aichat-search`
- **Cargo**: `cargo install aichat-search` (compiles from source, ~5 min)
- **Pre-built binary**: Download from [Releases](https://github.com/pchalasani/claude-code-tools/releases) (look for `rust-v*` releases)

That's it! No `npm install` needed — the Python package includes pre-installed Node.js dependencies.

Without `aichat-search`, search won't work, but other `aichat` commands (resume, trim, rollover, etc.) still function.

### What You Get

Four commands are installed:

| Command | Description |
|---------|-------------|
| [`aichat`](#aichat-session-management) | Continue work with session lineage and truncation, avoiding compaction;<br>fast (Rust/Tantivy) full-text session search TUI for humans, CLI for agents  |
| [`tmux-cli`](#tmux-cli-terminal-automation) | Terminal automation for AI agents ("Playwright for terminals") |
| [`vault`](#vault) | Encrypted .env backup and sync |
| [`env-safe`](#env-safe) | Safe .env inspection without exposing values |

<a id="claude-code-plugins"></a>

### Claude Code Plugins

<details>
<summary>📦 <b>Click to expand plugin details</b></summary>

This repo also provides plugins for the
[Claude Code marketplace](https://code.claude.com/docs/en/discover-plugins):

| Plugin | Description |
|--------|-------------|
| `aichat` | hooks (`>resume`), commands, skills, agents for continuing session work and fast full-text search of sessions|
| `tmux-cli` | Terminal automation skill for interacting with other tmux panes |
| `workflow` | Work logging, code walk-through, issue specs, UI testing |
| `safety-hooks` | Prevent destructive git/docker/rm commands |
| `langroid` | Design patterns for the [Langroid](https://github.com/langroid/langroid) multi-agent LLM framework |
| `voice` | Spoken audio summaries when agent stops; uses [pocket-tts](https://github.com/kyutai-labs/pocket-tts) |

**Install the plugins:**

First, add the marketplace (from terminal or within a Claude Code session):

```bash
claude plugin marketplace add pchalasani/claude-code-tools   # CLI
/plugin marketplace add pchalasani/claude-code-tools         # in-session
```

This creates the `cctools-plugins` plugin group. Then install plugins from it:

```bash
# CLI
claude plugin install "aichat@cctools-plugins"
claude plugin install "tmux-cli@cctools-plugins"
claude plugin install "workflow@cctools-plugins"
claude plugin install "safety-hooks@cctools-plugins"
claude plugin install "langroid@cctools-plugins"
claude plugin install "voice@cctools-plugins"

# Or in-session
/plugin install aichat@cctools-plugins
/plugin install tmux-cli@cctools-plugins
/plugin install workflow@cctools-plugins
/plugin install safety-hooks@cctools-plugins
/plugin install langroid@cctools-plugins
/plugin install voice@cctools-plugins
```

You can also use `/plugin` without arguments to launch a TUI for browsing and installing.

<a id="aichat-plugin-details"></a>

#### aichat Plugin Details

The `aichat` plugin provides:

| Type | Name | What it does |
|------|------|--------------|
| Hook | `>resume` | Type `>resume` (or `>continue`, `>handoff`) to copy session ID + show resume instructions |
| Hook | `>session` | Type `>session` (or `>session-id`) to copy session ID to clipboard |
| Skill | `/session-search` | Search past sessions (for agents without sub-agent support, e.g. Codex) |
| Skill | `/recover-context` | Extract context from parent sessions into current conversation |
| Agent | `session-searcher` | Sub-agent to search/retrieve context from past sessions |

#### tmux-cli Plugin Details

The `tmux-cli` plugin provides:

| Type | Name | What it does |
|------|------|--------------|
| Skill | `/tmux-cli` | Interact with CLI apps/agents in other tmux panes |

#### safety-hooks Plugin Details

The `safety-hooks` plugin provides hooks that block or require approval for dangerous operations:

| Hook | What it blocks/modifies |
|------|------------------------|
| `rm` protection | Blocks `rm -rf` on critical paths, requires approval for others |
| `git add` protection | Blocks `git add -A`, requires approval for modified files |
| `git checkout` protection | Warns before discarding uncommitted changes |
| `git commit` protection | Blocks commits without user review |
| `.env` protection | Blocks all read/write/edit of `.env` files; suggests `env-safe` CLI |
| File length limit | Blocks reading files >500 lines to prevent context bloat |

#### Workflow Plugin Details

The `workflow` plugin provides:

| Skill/Agent | What it does |
|-------------|--------------|
| `/code-walk-thru` | Walk through files in your editor to explain code or show changes |
| `/log-work` | Log work progress to `WORKLOG/YYYYMMDD.md` |
| `/make-issue-spec` | Create task specs at `issues/YYYYMMDD-topic.md` |
| `ui-tester` agent | Browser-based UI testing via Chrome DevTools MCP |

#### Langroid Plugin Details

The `langroid` plugin provides design pattern knowledge for the
[Langroid](https://github.com/langroid/langroid) multi-agent LLM framework:

| Skill | What it does |
|-------|--------------|
| `/patterns` | Index of Langroid design patterns (agent config, tools, task control, MCP integration) |
| `/add-pattern` | Record new patterns as you learn them from the Langroid codebase |

#### Voice Plugin Details

See the [Voice Plugin](#voice) section below for details.

</details>

---

<a id="aichat-session-management"></a>
# 💬 aichat — Session Search, and Continuation without Compaction

## Why I built this

This probably belongs in a blog post or reddit post, but I think knowing the thought process and motivation helps understand what the `aichat` command-group does and why it might be useful to you.
(For those wondering, this section is one
of the few parts of the entire repo that is 100% hand-crafted since I just cannot
trust today's LLMs to write just the way I want.)

<details>
<summary>📖 <b>Click to expand the full background</b></summary>

#### Compaction is lossy: instead, clone the session and truncate long tool-results and older assistant messages

So, here's how this all started. Session compaction is
**lossy:** there are very often situations where compaction loses important details,
so I wanted to find ways to continue my work without compaction.
A typical scenario is this -- I am at 90% context usage, and I wish I can go on a bit longer to finish the current work-phase. So I thought,
> I wish I could **truncate** some long tool results (e.g. file reads or API results) or older assistant messages (can include write/edit tool-calls) and clear out some context to continue my work.

This lead to the [`aichat trim`](#three-resume-strategies) utility. It provides two variants:

- a "blind" [`trim`](#three-resume-strategies) mode that truncates all tool-results longer than a threshold (default 500 chars), and optionally all-but-recent assistant messages (which may include long write/edit tool-calls)
-- all user-configurable. This can free up 40-60% context, depending on what's been going on in the session.

- a [`smart-trim`](#three-resume-strategies) mode that uses a headless Claude/Codex 
agent to determine which
messages can be safely truncated in order to continue the current work. The precise
truncation criteria can be customized (e.g. the user may want to continue some
prior work rather than the current task).


Both of these modes *clone* the current session before truncation, and inject two
types of [*lineage*](#lineage-nothing-is-lost):
- *Session-lineage* is injected into the first user message: a chronological listing
of sessions from which the current session was derived. This allows the (sub-) agent
to extract needed context from ancestor sessions, either when prompted by the user,
or on its own initiative.
- Each truncated message also carries a pointer to the specific message index in the parent session so full details can always be looked up if needed.

#### A cleaner alternative: Start new session with lineage and context summary

Session trimming can be a quick way to clear out context in order to continue the current task for a bit longer, but after a couple of trims, does not yield as much benefit. But the lineage-injection lead to a different idea to avoid compaction:

> Create a fresh session, inject parent-session lineage into the first user message, along with instructions to extract (using sub-agents if available) context of the latest
task from the parent session, or skip context extraction and leave it to the user to extract context once the session starts.

This is the idea behind the [`aichat rollover`](#three-resume-strategies) functionality, which is the variant I use the most frequently, and I use this
instead of first trimming a session. I usually choose
to skip the summarization (this is the `quick` rollover option in the TUI) so that
the new session starts quickly and I can instruct Claude-Code/Codex-CLI to extract
needed context (usually from the latest chat session shown in the lineage), as shown
in the [demo video](#resume-demo-video) below.

#### A hook to simplify continuing work from a session

I wanted to make it seamless to pick any of the above three task continuation modes, when inside a Claude Code session, so I set up a `UserPromptSubmit` [hook](#resume-options) (via the `aichat` plugin) that is triggered when the user types `>resume` (or `>continue` or `>handoff`). When I am close to full context usage,
I type `>resume`, and the hook script copies the current session id into the clipboard and shows instructions asking the user to run
`aichat resume <pasted-session-id>`; this launches a TUI that offering options to choose
one of the above [session resumption modes](#three-resume-strategies).
See the [demo video](#resume-demo-video) below.

#### Fast full-text session search for humans/agents to find prior work context

The above session resumption methods are useful to continue your work from the
*current* session, but often you want to continue work that was done in an
*older* Claude-Code/Codex-CLI session. This is why I added this:

> Super-fast Rust/Tantivy-based [full-text search](#aichat-search--find-and-select-sessions) of all sessions across Claude-Code and
Codex-CLI, with a pleasant self-explanatory TUI for humans, and a CLI mode for Agents
to find past work. (The Rust/Tantivy-based search and TUI was inspired by the excellent
TUI in the [zippoxer/recall](https://github.com/zippoxer/recall) repo).

Users can launch the search TUI using [`aichat search ...`](#aichat-search--find-and-select-sessions) and (sub-)
[agents can run](#agent-access-to-history-the-session-searcher-sub-agent)
`aichat search ... --json` and get results in JSONL format
for quick analysis and filtering using `jq` which of course CLI agents are
great at using. There is a corresponding *skill* called `session-search` and a *sub-agent* called `session-searcher`, both
available via the `aichat` [plugin](#claude-code-plugins).
For example in Claude Code,
users can recover context of some older work by simply saying something like:

> Use your session-searcher sub-agent to recover the context of how we worked on
connecting the Rust search TUI with the node-based Resume Action menus.

</details>

## Overview


`aichat` is your unified CLI command-group for managing Claude Code and Codex sessions.
Two main capabilities are available:

1. **Resume with lineage** — Continue sessions when context fills up, preserving
   links to parent sessions, avoiding lossy compaction.

2. **Search** — *Full-text search* across all sessions with a fast Rust/Tantivy-based 
TUI for humans, and CLI (with `--json` flag for jsonl output) for Codex or Claude (sub) 
Agent to search for past work. (Note that Claude Code's built-in search is not full-text
; it only searches the ad-hoc session titles created by CC, or renamed sessions). 

Examples:

```bash
aichat resume <session_id>     # Resume specific session with trim/rollover options
aichat resume                  # Resume latest session with trim/rollover options
aichat search "topic"          # Find sessions by keyword: for humans
aichat search "langroid mcp" --json # fast full-text search with jsonl output for agents
```

For detailed CLI options, run:
```bash
aichat --help              # See all subcommands
aichat <subcommand> --help # Help for specific subcommand
```

> [!NOTE]
> Most `aichat` commands accept `--claude-home` and `--codex-home` to override
> the default session directories (`~/.claude` and `~/.codex`). You can also set
> the `CLAUDE_CONFIG_DIR` and `CODEX_HOME` environment variables.

---

<a id="resume-options"></a>
## Resume Options — Continuing work in a trimmed or fresh session, with lineage.


You have three ways to access the resume functionality:

**1. In-session trigger** — This is likely to be used the most frequently: while already in a Claude Code session, when you're close to filling up context, type:

```bash
>resume # or >continue, >handoff; MUST include the ">" at the start
```

This triggers a `UserPromptSubmit` hook that blocks handling by Claude-Code 
(hence no further tokens consumed), copies the current session ID to your 
clipboard, and shows instructions to quit Claude Code and run `aichat resume <paste>`. 
This is a quick escape hatch when context is filling up — no need to manually find the 
session ID.

*Requires the `aichat` plugin. See [Claude Code Plugins](#claude-code-plugins)
for installation.*


<a id="resume-demo-video"></a>

https://github.com/user-attachments/assets/310dfa5b-a13b-4a2b-aef8-f73954ef8fe9



**2. [Search TUI](#aichat-search--find-and-select-sessions)** — Run `aichat search`, select a session, then choose a resume
action from the menu.

**3. Direct CLI** — Use these commands directly:

```bash
aichat resume abc123         # Resume specific session
aichat resume                # Auto-find latest for this project
```


---


### Three Resume Strategies

> [!TIP]
> It's highly recommended to turn off auto-compaction when using `aichat resume`.
> - **Claude Code:** Use the `/config` command to disable auto-compaction
> - **Codex CLI:** Set `model_auto_compact_token_limit = 0` in `~/.codex/config.toml`

When you access the resume menu using any of the above 3 mechanisms, you will
be presented with 3 resume strategies, as described below.
All strategies create a new session with **lineage** — links back to
parent sessions that the agent (or preferable a sub-agent if available)
can reference at any time.

**1. Trim + Resume**

Truncates large tool call results and assistant messages to free up space.
Quick and deterministic — you control what gets cut. The default is to trim
*all* tool results longer than 500 characters, and *none* of the
assistant messages. This can
often free up 30-50% of context when applied the first time to a normal session
(depending on what's in the session). A quick way to extend a session a bit
longer without lossy compaction.

The TUI lets you specify:

- Which tool types to truncate (e.g., bash, read, edit, or all)
- Length threshold in characters (default: 500)
- How many assistant messages to truncate 
  (N => first N, or -N => all except last N; defaults to 0). 
  For example to truncate all except the last 10 assistant messages, use `-10`.

Same options available via CLI: `aichat trim --help`

**2. Smart Trim + Resume**

Uses headless (non-interactive) Claude/Codex agent to analyze the session and
strategically identify what can user/assistant messages or tool results can 
be safely truncated without affecting the *last* task being worked on. Slower than 
deterministic trim, but smarter and more selective.

The TUI lets you specify:

- Message types to never trim (default: user messages)
- How many recent messages to always preserve (default: 10)
- Minimum content threshold for extraction (default: 200 chars)
- Custom instructions for what to prioritize when truncating

Same options available via CLI: `aichat smart-trim --help`

**3. Rollover**

The trim strategies work well once or twice but eventually stop freeing much
context. *Rollover* is a better alternative after a couple of trim iterations,
or directly from a normal session. This strategy hands off work to a fresh
session, injecting *session-lineage* pointers and an optional agent-generated summary of the current task. The session lineage pointers are a chronologically ordered
list of session jsonl file paths, of the parent session, parent's parent, and so on,
all the way back to the original session.
The new session typically starts with 15-20% context usage, 
and the agent or sub-agent can retrieve details from ancestor sessions on demand,
either if prompted by the user, or on its own when looking up prior work.


The TUI lets you specify:

- Which agent (Claude or Codex) to resume with — start in Claude Code, hand off
  to Codex for heavy refactoring, then back to Claude Code for finishing touches
- Rollover type:
  - **Quick rollover** — Just preserves lineage pointers, no context extraction.
    Fast, but you'll need to ask the agent to look up prior work as needed.
    If you install the `aichat` [plugin](#claude-code-plugins), you'll have access
    to the `/recover-context` command — the agent reads parent sessions and pulls
    relevant context into the current conversation.
  - **Rollover with context** — Uses a headless Claude/Codex agent to extract summary   
     of current work into the new session.
- Custom context recovery instructions (e.g., "focus on the authentication changes")
  — only available when using "Rollover with context"

Same options available via CLI: `aichat rollover --help` (use `--quick` for
quick mode, `-p "prompt"` for custom extraction instructions)

### Lineage: Nothing Is Lost

Unlike compaction (which permanently loses information), all strategies preserve
the complete parent session:

- **Lineage chain** — file paths of all ancestor sessions
- **On-demand retrieval** — agent can read any past session when needed

```
Original Session (abc123)
 └─► Trimmed/Rollover 1 (def456)
      └─► Trimmed/Rollover 2 (ghi789)
           └─► ... chain continues
```

See [here](docs/rollover-details.md) for details on how rollover works.

--- 

## aichat search — Find and Select Sessions

Uses Tantivy (Rust full-text search) to provide fast search across all your Claude and Codex sessions.

Here's what it looks like:

![aichat search demo](demos/aichat-search-asciinema.gif)

```bash
aichat search                      # Interactive TUI for current project
aichat search "langroid MCP"       # Pre-fill search query
aichat search -g                   # Global search (all projects)
aichat search --json -g "error"    # JSONL output for CLI-agents
```

**How it works:**

- **Auto-indexing:** Sessions are automatically indexed on startup—no manual
  export or build steps needed.
- **Self-explanatory TUI for humans:** Filter by session type, agent, date range, and more. All options are visible in the UI.
- **CLI options:** All search options are available as command-line arguments. Run
  `aichat search --help` for details.
- **JSON mode for Agents:** Use `--json` for JSONL output that CLI-agents can process with
  `jq` or other tools. See [Session-Searcher sub-agent](#agent-access-to-history-the-session-searcher-sub-agent), which is available
when you install the `aichat` plugin mentioned above.

**Session type filters:**

By default, search includes original, trimmed, and rollover sessions (but not
sub-agents). Use flags to customize:

```bash
aichat search                       # Default: original + trimmed + rollover
aichat search --sub-agent           # Add sub-agents to defaults
aichat search --no-original         # Exclude originals (show trimmed + rollover)
aichat search --no-trimmed          # Exclude trimmed (show original + rollover)
aichat search --sub-agent --no-rollover  # Add sub-agents, exclude rollovers
```

**Subtractive flags** (exclude from defaults): `--no-original`, `--no-trimmed`,
`--no-rollover`

**Additive flag** (add to defaults): `--sub-agent`

---

## Conceptual Flow: Search → Select → Actions

The typical workflow:

1. **Search** — Use `aichat search` to find sessions by keywords, date, or filters
2. **Select** — Choose a session from the results
3. **Actions** — Perform operations on the selected session

After selecting a session, you see the **actions menu**. This is equivalent to
running `aichat <session-id>` or `aichat menu <session-id>` directly.

**Session ID formats** (accepted by most commands):

- Full path: `~/.claude/projects/.../abc123.jsonl`
- Full ID: `abc123-def456-789-...`
- Partial ID: `abc123` (if unique)

---

## Session Actions

After selecting a session, the action menu offers:

- **Show path / Copy / Export** — File operations
- **Query** — Ask questions about the session using a headless Claude-Code/Codex agent
- **Resume options** — Various strategies for continuing work (see below)

---


### Agent Access to History; the Session-Searcher sub-agent

Your agent can search across all historical sessions using the JSON output
mode:

```bash
aichat search --json -g "error handling"  # Returns JSONL for programmatic use
aichat search --json --by-time            # Sort by last-modified time
```

This enables agents to find and retrieve context from any past session in the
lineage, either on their own initiative or when you prompt them to look up
historical context.

Installing the `aichat` plugin mentioned above provides two ways to search past sessions:

- **`Session-Searcher` sub-agent** (Claude Code) — A sub-agent with instructions to
  search a known session file if clear from context, or use `aichat search --json`
  to search past sessions. E.g. in Claude Code you can say:
  > From past sessions, recover details of our work on task-termination specification.

- **`session-search` skill** (Claude Code, Codex CLI) — A skill that invokes the same
  search functionality. Useful for CLI agents like Codex that don't yet support sub-agents.
  E.g. you can say:
  > Use your session-search skill to find our work on error handling.

---

## All Subcommands

| Command | Description |
|---------|-------------|
| `aichat search [query]` | Full-text search TUI across all sessions |
| `aichat menu [session]` | Interactive action menu for a session |
| `aichat resume [session]` | Resume options (resume, clone, trim, rollover) |
| `aichat info [session]` | Show session metadata, path, and lineage |
| `aichat export [session]` | Export session to text |
| `aichat copy [session]` | Copy session file to new location |
| `aichat move <session> <new-project>` | Move session to a different project directory |
| `aichat query [session] [question]` | Query session with AI |
| `aichat clone [session]` | Clone session and resume the clone |
| `aichat rollover [session]` | Hand off to fresh session with lineage |
| `aichat lineage [session]` | Show parent lineage chain |
| `aichat trim [session]` | Trim large tool outputs |
| `aichat smart-trim [session]` | AI-powered trimming (EXPERIMENTAL) |
| `aichat delete [session]` | Delete with confirmation |
| `aichat find-original [session]` | Trace back to original session |
| `aichat find-derived [session]` | Find all derived sessions |

**Index management:**

| Command | Description |
|---------|-------------|
| `aichat build-index` | Manually rebuild the search index |
| `aichat clear-index` | Clear the index for a fresh rebuild |
| `aichat index-stats` | Show index statistics and reconciliation |

The search index is powered by [Tantivy](https://github.com/quickwit-oss/tantivy)
(Rust full-text search). You typically don't need to manage it manually:

- **Auto-updates**: Index updates incrementally on every `aichat` command
- **Version rebuilds**: Index rebuilds automatically when the tool version changes
- **Manual rebuild**: Use `aichat clear-index && aichat build-index` if needed

Run `aichat <command> --help` for options

<a id="tmux-cli-terminal-automation"></a>
# 🎮 tmux-cli — Terminal Automation

> **Note**: While the description below focuses on Claude Code, tmux-cli works with any CLI coding agent.

![tmux-cli demo](demos/tmux-cli-demo-short.gif)

**Think Playwright for terminals** - Terminal automation for AI agents.

tmux-cli enables Claude Code to programmatically control terminal applications:
test interactive scripts, debug with pdb, launch and interact with other CLI agents.

**Important**: You don't need to learn tmux-cli commands. Claude Code handles
everything automatically—just describe what you want.

**Works anywhere**: Automatically handles both local tmux panes and remote sessions.

### Why tmux-cli instead of vanilla tmux?

Vanilla tmux can do everything tmux-cli does. The problem is that LLMs frequently make
mistakes with raw tmux: forgetting the Enter key, not adding delays between text and
Enter (causing race conditions with fast CLI apps), or incorrect escaping. `tmux-cli`
bakes in defaults that address these: Enter is sent automatically with a 1.5-second delay
(configurable), pane targeting accepts simple numbers instead of `session:window.pane`,
built-in `wait_idle` to detect when a CLI is ready for input, and **Enter key verification
with automatic retry** to handle intermittent failures.

> **Important: Instruct your AI agent to use `tmux-cli` instead of plain `tmux`**
>
> Add this directive to your project's `CLAUDE.md` or agent instructions:
>
> ```markdown
> When interacting with tmux panes, ALWAYS use `tmux-cli send` instead of plain
> `tmux send-keys`. Plain tmux commands are unreliable because they send text and
> Enter simultaneously without any delay, causing race conditions where the Enter
> key is lost before the target application can process the text input.
> ```
>
> **Why plain tmux is unreliable:** When you run `tmux send-keys "text" Enter`, both
> the text and Enter key are sent in rapid succession. If the target shell or application
> hasn't fully processed the text input buffer, the Enter key can be lost. This is
> especially common during shell initialization, with slow terminal emulators, or under
> system load. `tmux-cli` addresses this by:
> 1. Sending text first, then waiting 1.5 seconds before sending Enter
> 2. Verifying the Enter was received by checking if pane content changed
> 3. Automatically retrying the Enter key (up to 3 times) if verification fails

## Tmux-cli skill

To make it easier to have Claude-Code use this command, there's a **tmux-cli plugin** in this repo; once you install it, you can simply say "use your tmux-cli skill to get help from Codex running in tmux pane 3".

For detailed instructions, see [docs/tmux-cli-instructions.md](docs/tmux-cli-instructions.md) and [Claude Code tmux tutorials](docs/claude-code-tmux-tutorials.md).

All of this assumes you're familiar and comfortable with tmux, and (like me) run
all CLI coding sessions inside tmux sessions.


## What Claude Code Can Do With tmux-cli

1. **Test Interactive Scripts** - CC can run and interact with scripts that 
   require user input, answering prompts automatically based on your instructions.

2. **UI Development & Testing** - CC can launch web servers and coordinate with 
   browser automation tools to test your applications.

3. **Interactive Debugging** - CC can use debuggers (pdb, node inspect, gdb) to 
   step through code, examine variables, and help you understand program flow.

4. **Claude-to-Claude Communication** - CC can launch another Claude Code instance 
   to get specialized help or code reviews.


Claude Code can find out how to use tmux-cli through its built-in help. 
You just describe what you want, and CC handles the technical details.

For complete command reference, see [docs/tmux-cli-instructions.md](docs/tmux-cli-instructions.md).



<a id="lmsh-experimental"></a>
# 🚀 lmsh (Experimental)

Natural language shell - type what you want in plain English, get an editable command.

```bash
# Direct usage - translate, edit, execute, then enter interactive mode
$ lmsh "show me all python files modified today"
find . -name "*.py" -mtime 0  # <-- Edit before running

# Or interactive mode
$ lmsh
lmsh> show recent docker containers
docker ps -n 5  # <-- Edit before running
```

**Features:**
- Rust-based for instant startup (<1ms binary load time)
- Translates natural language to shell commands using Claude Code CLI
- Commands are editable before execution - full control
- Preserves your shell environment

**Note:** Requires Claude Code CLI (`claude` command) to be installed. The translation adds ~2-3s due to Claude Code CLI startup.

**Installation:**
```bash
# Install from crates.io (easiest, requires Rust)
cargo install lmsh

# Or build from source
cd lmsh && cargo build --release
cp target/release/lmsh ~/.cargo/bin/
# Or: make lmsh-install
```

See [docs/lmsh.md](docs/lmsh.md) for details.

<a id="status-line"></a>
## 📊 Status Line

A custom status line script for Claude Code is available at
[`scripts/statusline.sh`](scripts/statusline.sh). It displays model name,
project directory, git branch, git status indicators, and a context window
progress bar that changes color as you approach the limit.

![green](demos/statusline-green.png)
![yellow](demos/statusline-yellow.png)
![orange](demos/statusline-orange.png)
![red](demos/statusline-red.png)

To use it, copy the script and configure Claude Code:

```bash
cp scripts/statusline.sh ~/.claude/
chmod +x ~/.claude/statusline.sh
```

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh"
  }
}
```

Requires `jq` and a [Nerd Font](https://www.nerdfonts.com/) for powerline symbols.

<a id="vault"></a>
# 🔐 vault

Centralized encrypted backup for .env files across all your projects using SOPS.

```bash
vault sync      # Smart sync (auto-detect direction)
vault encrypt   # Backup .env to ~/Git/dotenvs/
vault decrypt   # Restore .env from centralized vault
vault list      # Show all project backups
vault status    # Check sync status for current project
```

### Key Features

- Stores all encrypted .env files in `~/Git/dotenvs/`
- Automatic sync direction detection
- GPG encryption via SOPS
- Timestamped backups for safety

For detailed documentation, see [docs/vault-documentation.md](docs/vault-documentation.md).

<a id="env-safe"></a>
# 🔍 env-safe

Safely inspect .env files without exposing sensitive values. Designed for Claude Code and other automated tools that need to work with environment files without accidentally leaking secrets.

```bash
env-safe list                    # List all environment variable keys
env-safe list --status           # Show keys with defined/empty status  
env-safe check API_KEY           # Check if a specific key exists
env-safe count                   # Count total, defined, and empty variables
env-safe validate                # Validate .env file syntax
env-safe --help                  # See all options
```

### Key Features

- **No Value Exposure** - Never displays actual environment values
- **Safe Inspection** - Check which keys exist without security risks
- **Syntax Validation** - Verify .env file format is correct
- **Status Checking** - See which variables are defined vs empty
- **Claude Code Integration** - Works with protection hooks to provide safe alternative

### Why env-safe?

The [`safety-hooks` plugin](#claude-code-safety-hooks) in this repo blocks Claude Code from directly accessing .env files — no reading, writing, or editing allowed. This prevents both accidental exposure of API keys and unintended modifications. The `env-safe` command provides the only approved way for Claude Code to inspect environment configuration safely, while any modifications must be done manually outside of Claude Code.


<a id="claude-code-safety-hooks"></a>
# 🛡️ Claude Code Safety Hooks

This repository includes a comprehensive set of safety hooks that enhance Claude
Code's behavior and prevent dangerous operations.

### Key Safety Features

- **File Deletion Protection** - Blocks `rm` commands, enforces TRASH directory
  pattern
- **Git Commit Protection** - Requires user approval before any git commit
  (uses Claude Code's permission prompt UI)
- **Git Add Protection** - Smart staging control:
  - Hard blocks: `git add .`, `git add ../`, `git add *`, `git add -A/--all`
  - New files: Allowed without permission
  - Modified files: Requires user approval (permission prompt)
  - Directories: Uses dry-run to detect files, asks permission if modified files
- **Environment Security** - Blocks all .env file operations (read/write/edit),
  suggests `env-safe` command for safe inspection
- **Context Management** - Blocks reading files >500 lines to prevent context
  bloat
- **Command Enhancement** - Enforces ripgrep (`rg`) over grep for better
  performance

### Installation

Install the `safety-hooks` plugin as described in
[Claude Code Plugins](#claude-code-plugins).

### Available Hooks

- `bash_hook.py` - Main hook that orchestrates all bash command checks
- `git_commit_block_hook.py` - User permission prompt for git commits
- `git_add_block_hook.py` - Smart staging: blocks dangerous patterns, prompts
  for modified files
- `env_file_protection_hook.py` - Blocks all .env file operations
- `file_size_conditional_hook.py` - Prevents reading huge files
- `grep_block_hook.py` - Enforces ripgrep usage
- `notification_hook.sh` - Sends ntfy.sh notifications

For complete documentation, see [hooks/README.md](hooks/README.md).

<a id="voice"></a>
# 🔊 Voice Plugin

Get spoken audio feedback when Claude Code completes a task. The agent
automatically speaks a 1-2 sentence summary before stopping, so you can
multitask while Claude works.

### Prerequisites

- **UV** - Required for running pocket-tts. Install via:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **FFmpeg** (recommended) - Enables streaming audio for lower latency. Install
  via `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux)

### How It Works

The plugin uses [pocket-tts](https://github.com/kyutai-labs/pocket-tts), a
lightweight text-to-speech library. On first use, it automatically:

1. Starts a pocket-tts server (via `uvx pocket-tts serve`)
2. Downloads the voice model (~50MB, one-time)

The server persists in the background (via `nohup`) so subsequent requests
are instant. Server logs are written to `/tmp/pocket-tts-server.log`.

To stop the server manually (works on both macOS and Linux):

```bash
pkill -f "pocket-tts serve"
```

### Installation

```bash
# Add the marketplace (if not already added)
claude plugin marketplace add pchalasani/claude-code-tools

# Install the plugin
claude plugin install voice@cctools-plugins
```

### Usage

Once installed, the plugin works automatically:

- **Automatic feedback**: When the agent finishes a task, it speaks a summary
  before stopping
- **Explicit requests**: Ask Claude to "use your voice" and it will speak
  its response

### Configuration

Use the `/voice:speak` command to configure:

```bash
/voice:speak                  # Enable voice feedback with current voice
/voice:speak alba             # Set voice to "alba" and enable feedback
/voice:speak azure            # Set voice to "azure" and enable
/voice:speak stop             # Disable voice feedback
/voice:speak prompt <text>    # Set custom instruction for summaries
/voice:speak prompt           # Clear custom prompt
```

**Custom prompts** let you personalize how summaries are delivered:

```bash
/voice:speak prompt "be upbeat and encouraging"
/voice:speak prompt "always end with 'back to you, boss'"
```

See the [pocket-tts repo](https://github.com/kyutai-labs/pocket-tts) for the
latest available voices.

### Architecture

The plugin uses two hooks working together:

- **Stop hook**: Blocks the agent from stopping until voice feedback is
  provided
- **PostToolUse hook**: Tracks when the `say` script is called and creates
  session-specific flags to prevent double-voice

This ensures:

- Normal Q&A gets voice feedback when the agent stops
- Explicit "use your voice" requests don't trigger double feedback
- Multiple concurrent sessions don't interfere with each other

<a id="using-claude-code-with-open-weight-anthropic-api-compatible-llm-providers"></a>
## 🤖 Using Claude Code with Open-weight Anthropic API-compatible LLM Providers

You can use Claude Code with alternative LLMs served via Anthropic-compatible
APIs, e.g. Kimi-k2, GLM4.5 (from zai), Deepseek-v3.1, [MiniMax-M2.1](https://platform.minimax.io/docs/guides/text-ai-coding-tools).
Add these functions to your shell config (.bashrc/.zshrc):

```bash
kimi() {
    (
        export ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic
        export ANTHROPIC_AUTH_TOKEN=$KIMI_API_KEY
        claude "$@"
    )
}

zai() {
    (
        export ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
        export ANTHROPIC_AUTH_TOKEN=$Z_API_KEY
        claude "$@"
    )
}

dseek() {
    (
        export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
        export ANTHROPIC_AUTH_TOKEN=${DEEPSEEK_API_KEY}
        export ANTHROPIC_MODEL=deepseek-chat
        export ANTHROPIC_SMALL_FAST_MODEL=deepseek-chat
        claude "$@"
    )
}

ccmm() {
    (
        export ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
        export ANTHROPIC_AUTH_TOKEN=$MINIMAX_API_KEY
        export API_TIMEOUT_MS=3000000
        export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
        export ANTHROPIC_MODEL=MiniMax-M2.1
        export ANTHROPIC_SMALL_FAST_MODEL=MiniMax-M2.1
        export ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M2.1
        export ANTHROPIC_DEFAULT_OPUS_MODEL=MiniMax-M2.1
        export ANTHROPIC_DEFAULT_HAIKU_MODEL=MiniMax-M2.1
        claude "$@"
    )
}
```

After adding these functions:
- Set your API keys: `export KIMI_API_KEY=your-kimi-key`,
  `export Z_API_KEY=your-z-key`, `export DEEPSEEK_API_KEY=your-deepseek-key`,
  `export MINIMAX_API_KEY=your-minimax-key`
- Run `kimi` to use Claude Code with the Kimi K2 LLM
- Run `zai` to use Claude Code with the GLM-4.5 model
- Run `dseek` to use Claude Code with the DeepSeek model
- Run `ccmm` to use Claude Code with the MiniMax M2.1 model

The functions use subshells to ensure the environment variables don't affect
your main shell session, so you could be running multiple instances of Claude Code,
each using a different LLM.

### Using Claude Code and Codex with Local LLMs

You can run **Claude Code** and **OpenAI Codex CLI** with local models using
[llama.cpp](https://github.com/ggml-org/llama.cpp)'s server for fully offline usage.

- **Claude Code** uses the Anthropic-compatible `/v1/messages` endpoint with models
  like GPT-OSS-20B, Qwen3-Coder-30B, Qwen3-Next-80B, and Nemotron-3-Nano
- **Codex CLI** uses the OpenAI-compatible `/v1/chat/completions` endpoint with GPT-OSS

For complete setup instructions including llama-server commands, config files, and
command-line options for switching models, see
**[docs/local-llm-setup.md](docs/local-llm-setup.md)**.

<a id="google-docs-tools"></a>
## 📝 Google Docs Tools (md2gdoc, gdoc2md)

When working with CLI agents like Claude-Code on writing projects, the most natural 
generation format is markdown. But iterating on these docs via Google Docs is a pain
since Gdocs are especially unfriendly to markdown:
uploading a Markdown file to Google Drive with proper formatting
is a pain. You have to:

1. Upload the `.md` file to Google Drive
2. Click "Open in Google Docs" to trigger conversion
3. This creates a *new* document with proper formatting
4. Delete the original raw `.md` file
5. Rename the new document

`md2gdoc` reduces this to one command. `gdoc2md` does the reverse.

### Installation

These tools require additional dependencies. Install with the `gdocs` extra:

```bash
uv tool install 'claude-code-tools[gdocs]'
```

### First-Time Setup (One-Time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create/select a project and enable the **Google Drive API**
3. Go to **APIs & Services** → **Credentials** → **Create Credentials** →
   **OAuth client ID**
4. Choose **Desktop app**, then download the JSON file
5. Save it as `.gdoc-credentials.json` in your project directory
6. First run will open browser for OAuth consent (one-time per project)

Credentials are project-specific (`.gdoc-credentials.json` and `.gdoc-token.json`
in the current directory), so different projects can use different Google
accounts.

### md2gdoc — Markdown to Google Docs

Upload Markdown files as native Google Docs:

```bash
md2gdoc report.md                           # Upload to root of Drive
md2gdoc report.md --folder "Perf/Reports"    # Upload to specific folder
md2gdoc report.md --name "Q4 Summary"       # Upload with custom name
```

If a file with the same name exists, `--on-existing` controls behavior:
- `ask` (default): prompt for action
- `version`: auto-add suffix (`report-1`, `report-2`, etc.)
- `overwrite`: replace existing file

Features:

- Native markdown conversion (same quality as manual upload + "Open in Docs")
- Follows Drive shortcuts to shared folders
- Conflict detection with version suffix or overwrite options
- Creates folders if they don't exist

### gdoc2md — Google Docs to Markdown

Download Google Docs as Markdown files:

```bash
gdoc2md "My Document"                        # Download from root
gdoc2md "My Document" --folder "PNL/Reports" # Download from folder
gdoc2md "My Document" -o report.md           # Save with custom name
gdoc2md --list --folder PNL                  # List docs in folder
```

<a id="development"></a>
## 🛠️ Development

### Architecture

The `aichat` command has three layers:

- **Python** (`claude_code_tools/`) - CLI entry points, backend logic, session parsing
- **Rust** (`rust-search-ui/`) - Search TUI with Tantivy full-text search
- **Node.js** (`node_ui/`) - Action menus (resume, export, trim, etc.)

Flow: Python CLI (`aichat search`) invokes Rust binary → Rust TUI for search →
user selects session → hands off to Node.js menus → menus call Python backend.

### Prerequisites

- **UV** - `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Rust/Cargo** - `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- **Node.js 16+** - Required for action menus

### Setup

```bash
git clone https://github.com/pchalasani/claude-code-tools
cd claude-code-tools
uv venv --python 3.11
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv sync
cd node_ui && npm install && cd ..
make install                  # Python (editable mode)
make aichat-search-install    # Rust binary
```

### Testing Changes

- **Python**: No action needed (editable mode - changes apply immediately)
- **Node.js**: No action needed (runs directly from `node_ui/`)
- **Rust**: Run `make aichat-search-install` to rebuild and install

### Publishing (Python Package)

For releasing to PyPI:

```bash
make all-patch   # Bump patch, push, GitHub release, build
make all-minor   # Bump minor, push, GitHub release, build
make all-major   # Bump major, push, GitHub release, build
uv publish       # Publish to PyPI (after any of the above)
```

These commands automatically:

1. Run `make prep-node` to ensure `node_ui/node_modules/` is up-to-date
2. Bump version → push to GitHub → create GitHub release
3. Build package (includes `node_modules/` so users don't need `npm install`)

Then run `uv publish` to upload to PyPI.

**Note:** Users need Node.js 16+ installed to run `aichat` action menus, but
they do NOT need npm — the package includes pre-installed dependencies.

### Publishing (Rust Binaries)

```bash
make aichat-search-publish  # Bump version and publish to crates.io
make lmsh-publish           # Bump version and publish to crates.io
```

### Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Test thoroughly
5. Commit your changes
6. Push to your fork
7. Open a Pull Request

### Available Make Commands

Run `make help` for full list. Key commands:

| Command | Description |
|---------|-------------|
| `make install` | Install Python in editable mode |
| `make aichat-search-install` | Build and install Rust binary |
| `make prep-node` | Install node_modules (auto-runs before publish) |
| `make all-patch/minor/major` | Bump + push + build (for PyPI) |
| `make aichat-search-publish` | Publish Rust binary to crates.io |

<a id="license"></a>
## 📄 License

MIT
