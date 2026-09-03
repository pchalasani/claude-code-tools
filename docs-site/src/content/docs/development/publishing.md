---
title: "Publishing"
description: >
  How to publish Python packages to PyPI and Rust
  binaries to crates.io.
---

## Python Packages (PyPI)

The repository publishes three independent Python distributions. The root
package is `claude-code-tools`; `voxtype` and `visual-brief` have their own
versions, tags, build artifacts, and PyPI releases.

All publish commands load `PYPI_TOKEN` from the primary Git checkout's `.env`
without printing it. Linked worktrees share that file. To load another dotenv
file, pass `PYPI_ENV_FILE=/path/to/file` to the publish command.

### claude-code-tools

Use the `all-*` Make commands to prepare a release, then publish:

```bash
make all-patch   # or all-minor, all-major
make publish
```

#### What the Commands Do

Each `all-*` command automatically:

1. Bumps the version (patch, minor, or major)
2. Pushes to GitHub and pushes tags
3. Creates a GitHub release
4. Cleans old builds and builds the package

The wheel build runs `npm ci` against `node_ui/package-lock.json` in a
temporary directory. It includes only locked production dependencies and does
not use or change the source tree's `node_modules/` directory. A missing or
inconsistent lock file stops the build.

The built package includes its Node dependencies, so users do not need to run
`npm install`. They need Node.js 18 or newer.

After the build completes, run `make publish` to upload to PyPI. It requires
both wheel and source distributions in `dist/`.

### Voxtype

Prepare a patch release, then publish its wheel and source distribution:

```bash
make voxtype-all-patch   # or voxtype-all-minor, voxtype-all-major
make voxtype-publish
```

The first command runs the Voxtype tests, bumps its independent version,
updates `uv.lock`, commits the bump, creates and pushes a `voxtype-vX.Y.Z` tag,
creates the GitHub release, and builds the package. The second command uploads
only `dist/voxtype-*` to PyPI.

To choose the bump and publish in one invocation, run:

```bash
make voxtype-all BUMP=patch   # or minor, major
```

Use `make voxtype-version` to print the current package version.

### Visual Brief

Prepare a patch release, then publish its wheel and source distribution:

```bash
make visual-brief-all-patch   # or -minor, -major
make visual-brief-publish
```

The first command runs the Visual Brief tests, bumps its independent version,
updates `uv.lock`, commits the bump, creates and pushes a
`visual-brief-vX.Y.Z` tag, creates the GitHub release, verifies the committed
browser bundle, and builds the package. The second command uploads only
`dist/visual_brief-*` to PyPI.

To choose the bump and publish in one invocation, run:

```bash
make visual-brief-all BUMP=patch   # or minor, major
```

Use `make visual-brief-version` to print the current package version. Rebuild
and commit changed browser assets with `make visual-brief-frontend` before
starting a release.

### Alternate PyPI Credentials

Pass another dotenv file to any publish target when needed:

```bash
make publish PYPI_ENV_FILE=~/.config/claude-code-tools/pypi.env
make voxtype-publish PYPI_ENV_FILE=~/.config/claude-code-tools/pypi.env
make visual-brief-publish PYPI_ENV_FILE=~/.config/claude-code-tools/pypi.env
```

## Rust Binaries (crates.io)

### aichat-search

```bash
make aichat-search-publish
```

This command:

1. Bumps the version (default: patch; override with
   `BUMP=minor` or `BUMP=major`)
2. Creates a `rust-v*` git tag
3. Pushes to GitHub (triggers CI for binary releases)
4. Publishes to crates.io

After publishing, users can install with:

```bash
cargo install aichat-search
```

Or via Homebrew:

```bash
brew install pchalasani/tap/aichat-search
```

### lmsh

```bash
make lmsh-publish
```

This command:

1. Bumps the patch version
2. Publishes to crates.io

After publishing, users can install with:

```bash
cargo install lmsh
```

## See Also

- [Make Commands](../make-commands/) -- full list of
  all available Make targets
- [Testing](../testing/) -- verify changes before
  publishing
