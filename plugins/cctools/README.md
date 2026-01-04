# cctools

Setup wizard for the claude-code-tools suite.

## What it does

This plugin provides a single skill `/cctools:setup` that walks you through
installing all CLI tools and plugins from the claude-code-tools repository.

## Usage

After installing this plugin, run:

```
/cctools:setup
```

Or just ask Claude: "set up my claude-code-tools"

Claude will guide you through:

1. **Prerequisites** - uv, cargo/brew, Node.js
2. **CLI tools** - the Python package with `aichat`, `tmux-cli`, `vault`, `env-safe`
3. **Rust binary** - `aichat-search` for fast session search (optional)
4. **Plugins** - choose which plugins you want:
   - `aichat` - session search, resume, context recovery
   - `tmux-cli` - terminal automation skill
   - `workflow` - work logging, code walk-through, issue specs
   - `safety-hooks` - protect against destructive commands

Each step explains what the tool does and asks for your permission before
installing.
