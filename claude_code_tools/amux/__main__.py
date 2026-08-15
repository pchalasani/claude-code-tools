"""Allow ``python -m claude_code_tools.amux`` (used by fzf reload bindings)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
