.PHONY: install release patch minor major dev-install help clean all-patch all-minor all-major release-github lmsh lmsh-install lmsh-publish aichat-search aichat-search-install aichat-search-release aichat-search-patch aichat-search-minor aichat-search-major aichat-search-publish fix-session-metadata fix-session-metadata-apply delete-helper-sessions delete-helper-sessions-apply prep-node update-homebrew

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
	@echo "  make aichat-search-publish BUMP=patch|minor|major - Bump, tag, push, publish to crates.io"
	@echo "  make update-homebrew VERSION=x.y.z - Update Homebrew formula manually"
	@echo "  make fix-session-metadata       - Scan for sessionId mismatches (dry-run)"
	@echo "  make fix-session-metadata-apply - Actually fix sessionId mismatches"
	@echo "  make delete-helper-sessions       - Find helper sessions to delete (dry-run)"
	@echo "  make delete-helper-sessions-apply - Actually delete helper sessions"
	@echo "  make prep-node    - Install node_modules (required before publishing)"

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

all-patch: prep-node
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
	@echo "Build complete! Ready for: uv publish --token YOUR_TOKEN"

all-minor: prep-node
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
	@echo "Build complete! Ready for: uv publish --token YOUR_TOKEN"

all-major: prep-node
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
	@echo "Build complete! Ready for: uv publish --token YOUR_TOKEN"

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
	@if [ -z "$(BUMP)" ]; then \
		echo "Usage: make aichat-search-publish BUMP=patch|minor|major"; \
		exit 1; \
	fi
	$(MAKE) aichat-search-$(BUMP)
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

prep-node:
	@echo "Installing Node.js dependencies for packaging..."
	@if ! command -v npm >/dev/null 2>&1; then \
		echo "Error: Node.js/npm not found. Install Node.js first."; \
		exit 1; \
	fi
	@cd node_ui && npm install
	@echo "node_ui/node_modules ready for packaging."

update-homebrew:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make update-homebrew VERSION=x.y.z"; \
		exit 1; \
	fi
	@./scripts/update-homebrew-formula.sh $(VERSION)
