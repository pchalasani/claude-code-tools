.PHONY: install release patch minor major dev-install help clean publish all-patch all-minor all-major release-github lmsh lmsh-install lmsh-publish aichat-search aichat-search-install aichat-search-release aichat-search-patch aichat-search-minor aichat-search-major aichat-search-publish fix-session-metadata fix-session-metadata-apply delete-helper-sessions delete-helper-sessions-apply update-homebrew docs-dev docs-build docs-preview voxtype-version voxtype-test voxtype-install voxtype-build voxtype-release voxtype-publish voxtype-all

GIT_PRIMARY_WORKTREE := $(realpath $(shell git rev-parse \
	--path-format=absolute --git-common-dir)/..)
PYPI_ENV_FILE ?= $(GIT_PRIMARY_WORKTREE)/.env

help:
	@echo "Available commands:"
	@echo "  make install      - Install in editable mode (for development)"
	@echo "  make dev-install  - Install with dev dependencies (includes commitizen)"
	@echo "  make release      - Bump patch version and install globally"
	@echo "  make patch        - Bump patch version (0.0.X) and install"
	@echo "  make minor        - Bump minor version (0.X.0) and install"
	@echo "  make major        - Bump major version (X.0.0) and install"
	@echo "  make all-patch    - Bump patch, push, GitHub release, build (ready for uv publish)"
	@echo "  make all-minor    - Bump minor, push, GitHub release, build (ready for uv publish)"
	@echo "  make all-major    - Bump major, push, GitHub release, build (ready for uv publish)"
	@echo "  make publish      - Publish dist/ using the primary checkout's .env"
	@echo "  make clean        - Clean build artifacts"
	@echo "  make release-github - Create GitHub release from latest tag"
	@echo "  make lmsh         - Build lmsh binary (requires Rust)"
	@echo "  make lmsh-install - Build and install lmsh to ~/.cargo/bin"
	@echo "  make lmsh-publish - Publish lmsh to crates.io"
	@echo "  make aichat-search         - Build aichat-search binary (requires Rust)"
	@echo "  make aichat-search-install - Build and install aichat-search to ~/.cargo/bin"
	@echo "  make aichat-search-patch   - Bump patch (0.0.X), tag, push"
	@echo "  make aichat-search-minor   - Bump minor (0.X.0), tag, push"
	@echo "  make aichat-search-major   - Bump major (X.0.0), tag, push"
	@echo "  make aichat-search-publish [BUMP=patch|minor|major] - Bump (default: patch), tag, push, publish"
	@echo "  make update-homebrew VERSION=x.y.z - Update Homebrew formula manually"
	@echo "  make fix-session-metadata       - Scan for sessionId mismatches (dry-run)"
	@echo "  make fix-session-metadata-apply - Actually fix sessionId mismatches"
	@echo "  make delete-helper-sessions       - Find helper sessions to delete (dry-run)"
	@echo "  make delete-helper-sessions-apply - Actually delete helper sessions"
	@echo "  make voxtype-test    - Run the voxtype test suite"
	@echo "  make voxtype-install - Install voxtype tool in editable mode"
	@echo "  make voxtype-build   - Build voxtype wheel + sdist into dist/"
	@echo "  make voxtype-release [BUMP=patch|minor|major] - Bump, tag voxtype-vX.Y.Z, push, GitHub release, build"
	@echo "  make voxtype-publish - Publish dist/voxtype-* to PyPI"
	@echo "  make voxtype-all [BUMP=...] - voxtype-release + voxtype-publish in one shot"

install:
	uv tool install --force -e .
	@echo "[node-ui] Note: Node-based alt UI uses node_ui/menu.js (no build step)."
	@echo "[node-ui] If you haven't yet: cd node_ui && npm install"
	@if command -v cargo >/dev/null 2>&1; then \
		echo "Building and installing lmsh..."; \
		cd lmsh && cargo build --release; \
		mkdir -p ~/.cargo/bin; \
		cp target/release/lmsh ~/.cargo/bin/; \
		echo "lmsh installed to ~/.cargo/bin/lmsh"; \
		if ! echo "$$PATH" | grep -q ".cargo/bin"; then \
			echo "⚠️  Add ~/.cargo/bin to your PATH if not already there"; \
		fi; \
	else \
		echo "Rust/cargo not found - skipping lmsh installation"; \
		echo "To install lmsh later, run: make lmsh-install"; \
	fi

install-gdocs:
	uv tool install --force -e ".[gdocs]"

dev-install:
	uv pip install -e ".[dev]"

release: patch

patch:
	@echo "Bumping patch version..."
	uv run cz bump --increment PATCH --yes
	uv tool install --force --reinstall .
	@echo "Installation complete!"

minor:
	@echo "Bumping minor version..."
	uv run cz bump --increment MINOR --yes
	uv tool install --force --reinstall .
	@echo "Installation complete!"

major:
	@echo "Bumping major version..."
	uv run cz bump --increment MAJOR --yes
	uv tool install --force --reinstall .
	@echo "Installation complete!"

clean:
	@echo "Cleaning build artifacts..."
	rm -rf dist/*
	@echo "Clean complete!"

publish:
	@if ! ls dist/*.whl dist/*.tar.gz >/dev/null 2>&1; then \
		echo "Error: dist/ must contain both wheel and source distributions" >&2; \
		exit 1; \
	fi
	@if [ ! -f "$(PYPI_ENV_FILE)" ]; then \
		echo "Error: PyPI environment file not found: $(PYPI_ENV_FILE)" >&2; \
		exit 1; \
	fi
	@uv run --no-sync --env-file "$(PYPI_ENV_FILE)" -- sh -eu -c '\
		if [ -z "$${PYPI_TOKEN:-}" ]; then \
			echo "Error: PYPI_TOKEN is not defined in $(PYPI_ENV_FILE)" >&2; \
			exit 1; \
		fi; \
		UV_PUBLISH_TOKEN="$$PYPI_TOKEN" uv publish'

all-patch:
	@echo "Ensuring dev dependencies (commitizen)..."
	@uv sync --extra dev --quiet
	@echo "Bumping patch version..."
	uv run cz bump --increment PATCH --yes
	@echo "Pushing to GitHub..."
	git push && git push --tags
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	gh release create v$$VERSION --title "v$$VERSION" || echo "Release v$$VERSION already exists"
	@echo "Cleaning old builds..."
	rm -rf dist/*
	@echo "Building package..."
	uv build
	@echo "Build complete! Ready for: make publish"

all-minor:
	@echo "Ensuring dev dependencies (commitizen)..."
	@uv sync --extra dev --quiet
	@echo "Bumping minor version..."
	uv run cz bump --increment MINOR --yes
	@echo "Pushing to GitHub..."
	git push && git push --tags
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	gh release create v$$VERSION --title "v$$VERSION" || echo "Release v$$VERSION already exists"
	@echo "Cleaning old builds..."
	rm -rf dist/*
	@echo "Building package..."
	uv build
	@echo "Build complete! Ready for: make publish"

all-major:
	@echo "Ensuring dev dependencies (commitizen)..."
	@uv sync --extra dev --quiet
	@echo "Bumping major version..."
	uv run cz bump --increment MAJOR --yes
	@echo "Pushing to GitHub..."
	git push && git push --tags
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	gh release create v$$VERSION --title "v$$VERSION" || echo "Release v$$VERSION already exists"
	@echo "Cleaning old builds..."
	rm -rf dist/*
	@echo "Building package..."
	uv build
	@echo "Build complete! Ready for: make publish"

release-github:
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	gh release create v$$VERSION --title "v$$VERSION"
	@echo "GitHub release created!"

lmsh:
	@echo "Building lmsh..."
	@cd lmsh && cargo build --release
	@echo "lmsh built at: lmsh/target/release/lmsh"

lmsh-install: lmsh
	@echo "Installing lmsh to ~/.cargo/bin..."
	@mkdir -p ~/.cargo/bin
	@cp lmsh/target/release/lmsh ~/.cargo/bin/
	@echo "lmsh installed to ~/.cargo/bin/lmsh"
	@if ! echo "$$PATH" | grep -q ".cargo/bin"; then \
		echo "⚠️  Add ~/.cargo/bin to your PATH if not already there"; \
	fi

lmsh-publish:
	@if ! command -v cargo-bump >/dev/null 2>&1; then \
		echo "Installing cargo-bump..."; \
		cargo install cargo-bump; \
	fi
	@echo "Bumping lmsh version..."
	@cd lmsh && cargo bump patch
	@echo "Publishing lmsh to crates.io..."
	@cd lmsh && cargo publish --allow-dirty
	@echo "Published! Users can now install with: cargo install lmsh"

aichat-search:
	@echo "Building aichat-search..."
	@cd rust-search-ui && cargo build --release
	@echo "aichat-search built at: rust-search-ui/target/release/aichat-search"

aichat-search-install: aichat-search
	@echo "Installing aichat-search to ~/.cargo/bin..."
	@mkdir -p ~/.cargo/bin
	@cp rust-search-ui/target/release/aichat-search ~/.cargo/bin/
	@echo "aichat-search installed to ~/.cargo/bin/aichat-search"
	@if ! echo "$$PATH" | grep -q ".cargo/bin"; then \
		echo "⚠️  Add ~/.cargo/bin to your PATH if not already there"; \
	fi

# Helper function for aichat-search release (used by patch/minor/major targets)
define aichat-search-bump
	@if ! command -v cargo-bump >/dev/null 2>&1; then \
		echo "Installing cargo-bump..."; \
		cargo install cargo-bump; \
	fi
	@echo "Bumping aichat-search $(1) version..."
	@cd rust-search-ui && cargo bump $(1)
	@VERSION=$$(grep "^version" rust-search-ui/Cargo.toml | head -1 | cut -d'"' -f2); \
	echo "Creating tag rust-v$$VERSION..."; \
	git add rust-search-ui/Cargo.toml rust-search-ui/Cargo.lock; \
	git commit -m "bump: aichat-search v$$VERSION"; \
	git tag "rust-v$$VERSION"; \
	git push && git push --tags
	@echo "Tag pushed! GitHub Actions will build and release binaries."
	@echo "Check progress at: https://github.com/pchalasani/claude-code-tools/actions"
endef

aichat-search-patch:
	$(call aichat-search-bump,patch)

aichat-search-minor:
	$(call aichat-search-bump,minor)

aichat-search-major:
	$(call aichat-search-bump,major)

# Backwards compatible alias
aichat-search-release: aichat-search-patch

aichat-search-publish:
	@BUMP_TYPE=$${BUMP:-patch}; \
	echo "Bumping $$BUMP_TYPE version..."; \
	$(MAKE) aichat-search-$$BUMP_TYPE
	@echo "Publishing aichat-search to crates.io..."
	@cd rust-search-ui && cargo publish --allow-dirty
	@echo "Published! Users can now install with: cargo install aichat-search"

fix-session-metadata:
	@echo "Scanning for sessionId mismatches (dry-run)..."
	@python3 scripts/fix_session_metadata.py --dry-run
	@echo ""
	@echo "To apply fixes: make fix-session-metadata-apply"
	@echo "Custom paths: CLAUDE_CONFIG_DIR=/path make fix-session-metadata"

fix-session-metadata-apply:
	@echo "Fixing sessionId mismatches..."
	@python3 scripts/fix_session_metadata.py -v

delete-helper-sessions:
	@echo "Scanning for helper sessions (dry-run)..."
	@python3 scripts/delete_helper_sessions.py --dry-run -v
	@echo ""
	@echo "To delete: make delete-helper-sessions-apply"

delete-helper-sessions-apply:
	@echo "Deleting helper sessions..."
	@python3 scripts/delete_helper_sessions.py -v

update-homebrew:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make update-homebrew VERSION=x.y.z"; \
		exit 1; \
	fi
	@./scripts/update-homebrew-formula.sh $(VERSION)

docs-dev:
	@echo "Starting docs dev server..."
	@cd docs-site && npm run dev

docs-build:
	@echo "Building docs..."
	@cd docs-site && npm run build
	@echo "Docs built to docs-site/dist/"

docs-preview:
	@echo "Previewing docs..."
	@cd docs-site && npm run preview

# ---------------------------------------------------------------------------
# voxtype (packages/voxtype) — standalone voice-dictation package
# ---------------------------------------------------------------------------

VOXTYPE_DIR := packages/voxtype
VOXTYPE_PYPROJECT := $(VOXTYPE_DIR)/pyproject.toml

define VOXTYPE_BUMP_PY
import pathlib, re, sys

part = sys.argv[1]
path = pathlib.Path("packages/voxtype/pyproject.toml")
text = path.read_text()
m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', text, re.M)
major, minor, patch = map(int, m.groups())
if part == "major":
    major, minor, patch = major + 1, 0, 0
elif part == "minor":
    minor, patch = minor + 1, 0
else:
    patch += 1
new = f"{major}.{minor}.{patch}"
path.write_text(text[: m.start()] + f'version = "{new}"' + text[m.end():])
print(new)
endef
export VOXTYPE_BUMP_PY

voxtype-version:
	@grep '^version' $(VOXTYPE_PYPROJECT) | head -1 | cut -d'"' -f2

voxtype-test:
	uv run pytest $(VOXTYPE_DIR)/tests -q

voxtype-install:
	uv tool install --force -e $(VOXTYPE_DIR)

voxtype-build:
	@echo "Cleaning old voxtype builds..."
	rm -f dist/voxtype-*
	@echo "Building voxtype..."
	uv build --package voxtype
	@echo "Build complete! Ready for: make voxtype-publish"

# Bump (BUMP=patch|minor|major, default patch), commit, tag voxtype-vX.Y.Z,
# push, create GitHub release, build. Then: make voxtype-publish
voxtype-release: voxtype-test
	@BUMP_TYPE=$${BUMP:-patch}; \
	OLD=$$(grep '^version' $(VOXTYPE_PYPROJECT) | head -1 | cut -d'"' -f2); \
	NEW=$$(python3 -c "$$VOXTYPE_BUMP_PY" $$BUMP_TYPE); \
	echo "Bumping voxtype $$OLD -> $$NEW ($$BUMP_TYPE)..."; \
	uv lock; \
	git add $(VOXTYPE_PYPROJECT) uv.lock; \
	git commit -m "bump: voxtype $$OLD → $$NEW"; \
	git tag "voxtype-v$$NEW"; \
	echo "Pushing to GitHub..."; \
	git push && git push --tags; \
	echo "Creating GitHub release..."; \
	gh release create "voxtype-v$$NEW" --title "voxtype v$$NEW" \
		--notes "voxtype $$NEW — install with: uv tool install voxtype" \
		|| echo "Release voxtype-v$$NEW already exists"
	$(MAKE) voxtype-build

voxtype-publish:
	@if ! ls dist/voxtype-*.whl dist/voxtype-*.tar.gz >/dev/null 2>&1; then \
		echo "Error: dist/ must contain voxtype wheel and sdist (run make voxtype-build)" >&2; \
		exit 1; \
	fi
	@if [ ! -f "$(PYPI_ENV_FILE)" ]; then \
		echo "Error: PyPI environment file not found: $(PYPI_ENV_FILE)" >&2; \
		exit 1; \
	fi
	@uv run --no-sync --env-file "$(PYPI_ENV_FILE)" -- sh -eu -c '\
		if [ -z "$${PYPI_TOKEN:-}" ]; then \
			echo "Error: PYPI_TOKEN is not defined in $(PYPI_ENV_FILE)" >&2; \
			exit 1; \
		fi; \
		UV_PUBLISH_TOKEN="$$PYPI_TOKEN" uv publish dist/voxtype-*'

# One-shot: bump + tag + push + GitHub release + build + publish to PyPI
voxtype-all: voxtype-release voxtype-publish
	@echo "voxtype released and published!"
