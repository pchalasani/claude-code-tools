---
title: "Make Commands"
description: >
  All available Make commands for building, testing,
  and publishing claude-code-tools.
---

## Overview

Run `make help` for the full list of available
commands. Below they are grouped by category.

## Installation

| Command | Description |
|---------|-------------|
| `make install` | Install Python in editable mode (for development) |
| `make install-gdocs` | Install with Google Docs extras |
| `make dev-install` | Install with dev dependencies (includes commitizen) |

## Version Bumping

These commands bump the version and install the
package, but do **not** push or publish:

| Command | Description |
|---------|-------------|
| `make patch` | Bump patch version (0.0.X) and install |
| `make minor` | Bump minor version (0.X.0) and install |
| `make major` | Bump major version (X.0.0) and install |
| `make release` | Alias for `make patch` |

## Publishing (Python)

These commands do everything needed to prepare a
PyPI release: bump version, push to GitHub, create
a GitHub release, and build the package.

| Command | Description |
|---------|-------------|
| `make all-patch` | Bump patch + push + GitHub release + build |
| `make all-minor` | Bump minor + push + GitHub release + build |
| `make all-major` | Bump major + push + GitHub release + build |
| `make publish` | Publish `dist/` using the primary checkout's `.env` |
| `make release-github` | Create GitHub release from latest tag |
| `make clean` | Clean `dist/` build artifacts |

After running any `all-*` command, publish with:

```bash
make publish
```

Linked worktrees automatically use the primary checkout's `.env`. Set
`PYPI_ENV_FILE=/path/to/file` to load the token from another dotenv file.

## Rust Binaries

### aichat-search

| Command | Description |
|---------|-------------|
| `make aichat-search` | Build the aichat-search binary |
| `make aichat-search-install` | Build and install to `~/.cargo/bin` |
| `make aichat-search-patch` | Bump patch (0.0.X), tag, push |
| `make aichat-search-minor` | Bump minor (0.X.0), tag, push |
| `make aichat-search-major` | Bump major (X.0.0), tag, push |
| `make aichat-search-publish` | Bump, tag, push, publish to crates.io |

:::note
`make aichat-search-publish` accepts an optional
`BUMP` parameter:

```bash
make aichat-search-publish BUMP=minor
```

Default bump type is `patch`.
:::

### lmsh

| Command | Description |
|---------|-------------|
| `make lmsh` | Build the lmsh binary |
| `make lmsh-install` | Build and install to `~/.cargo/bin` |
| `make lmsh-publish` | Bump version and publish to crates.io |

## Documentation

| Command | Description |
|---------|-------------|
| `make docs-dev` | Start the Starlight docs dev server (hot reload) |
| `make docs-build` | Build the docs site to `docs-site/dist/` |
| `make docs-preview` | Preview the built docs site locally |

## Maintenance

| Command | Description |
|---------|-------------|
| `make fix-session-metadata` | Scan for sessionId mismatches (dry-run) |
| `make fix-session-metadata-apply` | Fix sessionId mismatches |
| `make delete-helper-sessions` | Find helper sessions to delete (dry-run) |
| `make delete-helper-sessions-apply` | Delete helper sessions |
| `make update-homebrew VERSION=x.y.z` | Update Homebrew formula manually |
