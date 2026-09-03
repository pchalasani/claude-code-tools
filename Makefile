.PHONY: release-preflight install install-gdocs node-ui-deps release patch minor major dev-install help clean publish all-patch all-minor all-major release-github lmsh lmsh-install lmsh-publish aichat-search aichat-search-install aichat-search-release aichat-search-patch aichat-search-minor aichat-search-major aichat-search-publish fix-session-metadata fix-session-metadata-apply delete-helper-sessions delete-helper-sessions-apply update-homebrew docs-dev docs-build docs-preview voxtype-version voxtype-test voxtype-install voxtype-build voxtype-release voxtype-publish voxtype-all voxtype-all-patch voxtype-all-minor voxtype-all-major visual-brief-version visual-brief-frontend visual-brief-frontend-check visual-brief-test visual-brief-install visual-brief-build visual-brief-release visual-brief-publish visual-brief-all visual-brief-all-patch visual-brief-all-minor visual-brief-all-major

GIT_PRIMARY_WORKTREE := $(realpath $(shell git rev-parse \
	--path-format=absolute --git-common-dir)/..)
PYPI_ENV_FILE ?= $(GIT_PRIMARY_WORKTREE)/.env

help:
	@echo "Available commands:"
	@echo "  make install      - Install in editable mode (for development)"
	@echo "  make dev-install  - Install with dev dependencies (includes commitizen)"
	@echo "  make node-ui-deps - Install node_ui/ npm deps (needed by aichat menus)"
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
	@echo "  make voxtype-all-patch / -minor / -major - Bump that part, push, GitHub release, build (then: make voxtype-publish)"
	@echo "  make voxtype-publish - Publish dist/voxtype-* to PyPI"
	@echo "  make voxtype-all [BUMP=...] - voxtype-release + voxtype-publish in one shot"
	@echo "  make visual-brief-frontend - Build visual-brief browser/helper bundles (needs Node)"
	@echo "  make visual-brief-test    - Run the visual-brief test suite"
	@echo "  make visual-brief-install - Install visual-brief in editable mode"
	@echo "  make visual-brief-build   - Build visual-brief wheel and sdist"
	@echo "  make visual-brief-release [BUMP=patch|minor|major] - Release visual-brief"
	@echo "  make visual-brief-publish - Publish visual-brief artifacts to PyPI"
	@echo "  make visual-brief-all-patch / -minor / -major - Bump, release, and build (then: make visual-brief-publish)"
	@echo "  make visual-brief-all [BUMP=...] - Release and publish visual-brief in one shot"

node-ui-deps:
	@if command -v npm >/dev/null 2>&1; then \
		echo "[node-ui] Installing Node UI dependencies into node_ui/node_modules..."; \
		npm ci --prefix node_ui --omit=dev --no-audit --no-fund || \
			npm install --prefix node_ui --omit=dev --no-audit --no-fund; \
	else \
		echo "⚠️  [node-ui] npm not found - aichat's interactive menus will not run."; \
		echo "   Install Node.js/npm, then run: make node-ui-deps"; \
	fi

install: node-ui-deps
	uv tool install --force -e .
	@echo "[node-ui] Node-based UI runs from node_ui/menu.js (no build step)."
	@if command -v cargo >/dev/null 2>&1; then \
		echo "Building and installing lmsh..."; \
		cd lmsh && cargo build --release; \
		mkdir -p ~/.cargo/bin; \
		cp target/release/lmsh ~/.cargo/bin/.lmsh.new; \
		chmod 755 ~/.cargo/bin/.lmsh.new; \
		mv -f ~/.cargo/bin/.lmsh.new ~/.cargo/bin/lmsh; \
		echo "lmsh installed to ~/.cargo/bin/lmsh"; \
		if ! echo "$$PATH" | grep -q ".cargo/bin"; then \
			echo "⚠️  Add ~/.cargo/bin to your PATH if not already there"; \
		fi; \
	else \
		echo "Rust/cargo not found - skipping lmsh installation"; \
		echo "To install lmsh later, run: make lmsh-install"; \
	fi

install-gdocs: node-ui-deps
	uv tool install --force -e ".[gdocs]"

dev-install: node-ui-deps
	uv pip install -e ".[dev]"

# Refuse to cut a release from anything but an up-to-date, clean main.
# voxtype 0.1.7 was published to PyPI from a local main that had not
# pulled a merged fix: the bump commit landed on a stale tree, the push
# was rejected, the tag never reached GitHub — and the build and upload
# went ahead regardless. This guard makes that impossible.
release-preflight:
	@set -e; \
	branch=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$branch" != "main" ]; then \
		echo "ERROR: releases are cut from main (on '$$branch')" >&2; exit 1; \
	fi; \
	if [ -n "$$(git status --porcelain -uno)" ]; then \
		echo "ERROR: working tree has uncommitted changes" >&2; exit 1; \
	fi; \
	: "(untracked files are deliberately ignored: this repo keeps many)"; \
	git fetch -q origin main; \
	if [ "$$(git rev-parse HEAD)" != "$$(git rev-parse origin/main)" ]; then \
		echo "ERROR: local main is not origin/main (behind and/or ahead)." >&2; \
		echo "       git pull --ff-only first; releasing from a stale tree" >&2; \
		echo "       is how voxtype 0.1.7 shipped without its fix." >&2; \
		exit 1; \
	fi; \
	echo "preflight OK: main is clean and matches origin/main"

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

all-patch: release-preflight
	@echo "Ensuring dev dependencies (commitizen)..."
	@uv sync --extra dev --quiet
	@echo "Bumping patch version..."
	uv run cz bump --increment PATCH --yes
	@echo "Pushing to GitHub..."
	git push && git push --tags
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	if gh release view v$$VERSION >/dev/null 2>&1; then \
		echo "Release v$$VERSION already exists"; \
	else \
		gh release create v$$VERSION --title "v$$VERSION"; \
	fi
	@echo "Cleaning old builds..."
	rm -rf dist/*
	@echo "Building package..."
	uv build
	@echo "Build complete! Ready for: make publish"

all-minor: release-preflight
	@echo "Ensuring dev dependencies (commitizen)..."
	@uv sync --extra dev --quiet
	@echo "Bumping minor version..."
	uv run cz bump --increment MINOR --yes
	@echo "Pushing to GitHub..."
	git push && git push --tags
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	if gh release view v$$VERSION >/dev/null 2>&1; then \
		echo "Release v$$VERSION already exists"; \
	else \
		gh release create v$$VERSION --title "v$$VERSION"; \
	fi
	@echo "Cleaning old builds..."
	rm -rf dist/*
	@echo "Building package..."
	uv build
	@echo "Build complete! Ready for: make publish"

all-major: release-preflight
	@echo "Ensuring dev dependencies (commitizen)..."
	@uv sync --extra dev --quiet
	@echo "Bumping major version..."
	uv run cz bump --increment MAJOR --yes
	@echo "Pushing to GitHub..."
	git push && git push --tags
	@echo "Creating GitHub release..."
	@VERSION=$$(grep "^version" pyproject.toml | head -1 | cut -d'"' -f2); \
	if gh release view v$$VERSION >/dev/null 2>&1; then \
		echo "Release v$$VERSION already exists"; \
	else \
		gh release create v$$VERSION --title "v$$VERSION"; \
	fi
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
	@# Same atomic replace as aichat-search: see the note there.
	@cp lmsh/target/release/lmsh ~/.cargo/bin/.lmsh.new
	@chmod 755 ~/.cargo/bin/.lmsh.new
	@mv -f ~/.cargo/bin/.lmsh.new ~/.cargo/bin/lmsh
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
	@# Replace via a temp file + mv, not cp: overwriting a Mach-O binary in
	@# place leaves macOS holding a stale code signature for that inode, and
	@# the next exec is SIGKILLed ("killed: 9"). mv swaps in a fresh inode.
	@cp rust-search-ui/target/release/aichat-search ~/.cargo/bin/.aichat-search.new
	@chmod 755 ~/.cargo/bin/.aichat-search.new
	@mv -f ~/.cargo/bin/.aichat-search.new ~/.cargo/bin/aichat-search
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
	uv run --package voxtype pytest $(VOXTYPE_DIR)/tests -q

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
voxtype-release: release-preflight voxtype-test
	@set -e; \
	BUMP_TYPE=$${BUMP:-patch}; \
	OLD=$$(grep '^version' $(VOXTYPE_PYPROJECT) | head -1 | cut -d'"' -f2); \
	NEW=$$(python3 -c "$$VOXTYPE_BUMP_PY" $$BUMP_TYPE); \
	echo "Bumping voxtype $$OLD -> $$NEW ($$BUMP_TYPE)..."; \
	uv lock; \
	git add $(VOXTYPE_PYPROJECT) uv.lock; \
	git commit -m "bump: voxtype $$OLD → $$NEW"; \
	git tag "voxtype-v$$NEW"; \
	echo "Pushing to GitHub..."; \
	git push; \
	git push --tags; \
	echo "Creating GitHub release..."; \
	if gh release view "voxtype-v$$NEW" >/dev/null 2>&1; then \
		echo "Release voxtype-v$$NEW already exists"; \
	else \
		gh release create "voxtype-v$$NEW" --title "voxtype v$$NEW" \
			--notes "voxtype $$NEW — install with: uv tool install voxtype"; \
	fi
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
voxtype-all:
	@$(MAKE) voxtype-release
	@$(MAKE) voxtype-publish
	@echo "voxtype released and published!"

# Named bump aliases mirroring the umbrella's all-patch/minor/major:
# bump that version part, tag, push, GitHub release, build — then run
# `make voxtype-publish` to upload (matches `make all-patch && make publish`).
voxtype-all-patch:
	@$(MAKE) voxtype-release BUMP=patch

voxtype-all-minor:
	@$(MAKE) voxtype-release BUMP=minor

voxtype-all-major:
	@$(MAKE) voxtype-release BUMP=major

# ---------------------------------------------------------------------------
# visual-brief (packages/visual-brief) — local multi-session briefing server
# ---------------------------------------------------------------------------

VISUAL_BRIEF_DIR := packages/visual-brief
VISUAL_BRIEF_PYPROJECT := $(VISUAL_BRIEF_DIR)/pyproject.toml
VISUAL_BRIEF_INIT := $(VISUAL_BRIEF_DIR)/src/visual_brief/__init__.py

define VISUAL_BRIEF_BUMP_PY
import pathlib, re, sys

part = sys.argv[1]
path = pathlib.Path("packages/visual-brief/pyproject.toml")
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
init_path = pathlib.Path("packages/visual-brief/src/visual_brief/__init__.py")
init_text = init_path.read_text()
init_text = re.sub(
    r'^__version__ = "[^"]+"',
    f'__version__ = "{new}"',
    init_text,
    count=1,
    flags=re.M,
)
init_path.write_text(init_text)
print(new)
endef
export VISUAL_BRIEF_BUMP_PY

VISUAL_BRIEF_FRONTEND := $(VISUAL_BRIEF_DIR)/frontend
VISUAL_BRIEF_CODEX := plugins/dynamic-workflow
VISUAL_BRIEF_STAMP := $(VISUAL_BRIEF_DIR)/tools/frontend_stamp.py

visual-brief-version:
	@grep '^version' $(VISUAL_BRIEF_PYPROJECT) | head -1 | cut -d'"' -f2

# Rebuild the committed browser and Codex helper bundles. Installing needs no Node.
visual-brief-frontend:
	@command -v npm >/dev/null 2>&1 || { \
		echo "Error: npm is required to build the visual-brief front end" >&2; \
		echo "       (Vite needs Node >= 20.19; installing the tool needs none)" >&2; \
		exit 1; \
	}
	@HELPER_META=$$(mktemp); \
	trap 'rm -f "$$HELPER_META"' EXIT; \
	set -e; \
	(cd $(VISUAL_BRIEF_CODEX) && \
		npm ci --ignore-scripts --no-audit --no-fund); \
	(cd $(VISUAL_BRIEF_FRONTEND) && \
		npm ci --ignore-scripts --no-audit --no-fund && \
		npm run typecheck && npm test && npm run build); \
	(cd $(VISUAL_BRIEF_CODEX) && npm run build:visual-brief -- \
		--metafile="$$HELPER_META"); \
	python3 $(VISUAL_BRIEF_STAMP) write --helper-metadata "$$HELPER_META"
	@echo "Built bundle is committed package data: git add \
$(VISUAL_BRIEF_DIR)/src/visual_brief/static \
$(VISUAL_BRIEF_DIR)/tools/bundle-stamp.json"

# Refuse to test against a bundle that no longer matches its sources.
visual-brief-frontend-check:
	@python3 $(VISUAL_BRIEF_STAMP) check

visual-brief-test: visual-brief-frontend-check
	uv run --package visual-brief pytest \
		$(VISUAL_BRIEF_DIR)/tests -q

visual-brief-install:
	uv tool install --force -e $(VISUAL_BRIEF_DIR)

# Nothing leaves the machine without the committed bundle matching its sources.
visual-brief-build: visual-brief-frontend-check
	@echo "Cleaning old visual-brief builds..."
	rm -f dist/visual_brief-*
	@echo "Building visual-brief..."
	uv build --package visual-brief
	@echo "Build complete! Ready for: make visual-brief-publish"

visual-brief-release: visual-brief-test
	@BUMP_TYPE=$${BUMP:-patch}; \
	OLD=$$(grep '^version' $(VISUAL_BRIEF_PYPROJECT) | head -1 | cut -d'"' -f2); \
	NEW=$$(python3 -c "$$VISUAL_BRIEF_BUMP_PY" $$BUMP_TYPE); \
	echo "Bumping visual-brief $$OLD -> $$NEW ($$BUMP_TYPE)..."; \
	uv lock; \
	git add $(VISUAL_BRIEF_PYPROJECT) $(VISUAL_BRIEF_INIT) uv.lock; \
	git commit -m "bump: visual-brief $$OLD → $$NEW"; \
	git tag "visual-brief-v$$NEW"; \
	git push && git push --tags; \
	gh release create "visual-brief-v$$NEW" --title "visual-brief v$$NEW" \
		--notes "visual-brief $$NEW — install: uv tool install visual-brief" \
		|| echo "Release visual-brief-v$$NEW already exists"
	$(MAKE) visual-brief-build

visual-brief-publish: visual-brief-frontend-check
	@if ! ls dist/visual_brief-*.whl \
		dist/visual_brief-*.tar.gz >/dev/null 2>&1; then \
		echo "Error: build visual-brief before publishing" >&2; \
		exit 1; \
	fi
	@if [ ! -f "$(PYPI_ENV_FILE)" ]; then \
		echo "Error: PyPI environment file not found: $(PYPI_ENV_FILE)" >&2; \
		exit 1; \
	fi
	@uv run --no-sync --env-file "$(PYPI_ENV_FILE)" -- sh -eu -c '\
		if [ -z "$${PYPI_TOKEN:-}" ]; then \
			echo "Error: PYPI_TOKEN is not defined" >&2; \
			exit 1; \
		fi; \
		UV_PUBLISH_TOKEN="$$PYPI_TOKEN" uv publish dist/visual_brief-*'

# One-shot: bump + tag + push + GitHub release + build + publish to PyPI
visual-brief-all:
	@$(MAKE) visual-brief-release
	@$(MAKE) visual-brief-publish
	@echo "visual-brief released and published!"

# Named bump aliases matching the standalone Voxtype package workflow.
visual-brief-all-patch:
	@$(MAKE) visual-brief-release BUMP=patch

visual-brief-all-minor:
	@$(MAKE) visual-brief-release BUMP=minor

visual-brief-all-major:
	@$(MAKE) visual-brief-release BUMP=major
