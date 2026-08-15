"""Tests for lazy modules exported from the package root."""

import subprocess
import sys
from textwrap import dedent


def test_public_modules_are_lazy_and_preserve_identity() -> None:
    """An ordinary import should defer public module imports until access."""
    script = dedent(
        """
        import importlib
        import sys

        original_path = sys.path.copy()
        import claude_code_tools

        names = ("action_rpc", "env_safe", "config")
        assert claude_code_tools.__all__ == list(names)
        assert all(name in dir(claude_code_tools) for name in claude_code_tools.__all__)
        assert sys.path == original_path
        for name in names:
            qualified_name = f"claude_code_tools.{name}"
            assert qualified_name not in sys.modules
            assert name not in claude_code_tools.__dict__

            exported = getattr(claude_code_tools, name)

            assert exported is importlib.import_module(qualified_name)
            assert exported is sys.modules[qualified_name]
            assert getattr(claude_code_tools, name) is exported
        """
    )

    subprocess.run([sys.executable, "-c", script], check=True)
