<div align="center">

<a href="https://pchalasani.github.io/claude-code-tools/">
<img src="assets/logo-nyc-subway.jpg" alt="CLAUDE CODE TOOLS"
     width="500"/>
</a>

CLI tools, skills, agents, hooks, and plugins for enhancing productivity with Claude Code and other coding agents.

[![Documentation](https://img.shields.io/badge/%F0%9F%93%96-documentation-blue)](https://pchalasani.github.io/claude-code-tools/)
[![aichat-search](https://img.shields.io/github/v/release/pchalasani/claude-code-tools?filter=rust-v*&label=aichat-search&color=orange)](https://github.com/pchalasani/claude-code-tools/releases?q=rust)

</div>

## [Full Documentation](https://pchalasani.github.io/claude-code-tools/)

## Install

```bash
# Core package
uv tool install claude-code-tools

# With Google Docs/Sheets extras
uv tool install "claude-code-tools[gdocs]"

# Upgrade an existing installation
uv tool install --force claude-code-tools
```

The search engine (`aichat search`) requires a
separate Rust binary:

- **Homebrew** (macOS/Linux):
  `brew install pchalasani/tap/aichat-search`
- **Cargo**: `cargo install aichat-search`
- **Pre-built binary**:
  [Releases](https://github.com/pchalasani/claude-code-tools/releases)
  (look for `rust-v*`)

Install the Claude Code
[plugins](https://pchalasani.github.io/claude-code-tools/getting-started/plugins/)
for hooks, skills, and agents:

```bash
claude plugin marketplace add pchalasani/claude-code-tools
```

---

Click a card to jump to that feature, or
**[read the full docs](https://pchalasani.github.io/claude-code-tools/)**.

<div align="center">

<table>
<tr>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/getting-started/">
<img src="assets/card-quickstart.svg" alt="quick start" width="300"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/getting-started/plugins/">
<img src="assets/card-plugins.svg" alt="plugins" width="300"/>
</a>
</td>
</tr>
</table>

<table>
<tr>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/aichat/">
<img src="assets/card-aichat.svg" alt="aichat" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/tmux-cli/">
<img src="assets/card-tmux.svg" alt="tmux-cli" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/lmsh/">
<img src="assets/card-lmsh.svg" alt="lmsh" width="200"/>
</a>
</td>
</tr>
<tr>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/vault/">
<img src="assets/card-vault.svg" alt="vault" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/env-safe/">
<img src="assets/card-env-safe.svg" alt="env-safe" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/plugins-detail/safety-hooks/">
<img src="assets/card-safety.svg" alt="safety" width="200"/>
</a>
</td>
</tr>
<tr>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/statusline/">
<img src="assets/card-statusline.svg" alt="statusline" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/integrations/google-docs/">
<img src="assets/card-gdocs.svg" alt="gdocs" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/integrations/google-sheets/">
<img src="assets/card-gsheets.svg" alt="gsheets" width="200"/>
</a>
</td>
</tr>
<tr>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/integrations/alt-llm-providers/">
<img src="assets/card-alt.svg" alt="alt" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/plugins-detail/voice/">
<img src="assets/card-voice.svg" alt="voice" width="200"/>
</a>
</td>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/tools/fix-session/">
<img src="assets/card-session-repair.svg" alt="session repair" width="200"/>
</a>
</td>
</tr>
</table>

<table>
<tr>
<td align="center">
<a href="https://pchalasani.github.io/claude-code-tools/development/">
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

> **Legacy links** — The sections below exist to
> preserve links shared in earlier discussions.
> For current documentation, visit the
> [full docs site](https://pchalasani.github.io/claude-code-tools/).

<a id="aichat-session-management"></a>
## aichat — Session Management
See [aichat](https://pchalasani.github.io/claude-code-tools/tools/aichat/) in the full documentation.

<a id="tmux-cli-terminal-automation"></a>
## tmux-cli — Terminal Automation
See [tmux-cli](https://pchalasani.github.io/claude-code-tools/tools/tmux-cli/) in the full documentation.

<a id="voice"></a>
## Voice Plugin
See [Voice](https://pchalasani.github.io/claude-code-tools/plugins-detail/voice/) in the full documentation.

<a id="license"></a>
## License

MIT
