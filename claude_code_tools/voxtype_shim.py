"""Back-compat ``voice-type`` entry point.

The voice dictation tool now lives in the standalone ``voxtype``
package (installed via the ``voice`` / ``voice-parakeet`` /
``voice-mlx`` extras of claude-code-tools, or directly with
``uv tool install voxtype``). This shim keeps the old ``voice-type``
command working for existing claude-code-tools installs.
"""

from __future__ import annotations

import sys

_HINT = (
    "voice-type is now the standalone 'voxtype' package.\n"
    "Install it directly:\n"
    "  uv tool install voxtype\n"
    "or via the umbrella extra:\n"
    '  uv tool install "claude-code-tools[voice]"'
)


def main() -> None:
    """Run the voxtype CLI, or explain how to install it."""
    try:
        from voxtype.cli import main as voxtype_main
    except ImportError:
        print(_HINT, file=sys.stderr)
        sys.exit(1)
    voxtype_main()
