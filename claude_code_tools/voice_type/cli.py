#!/usr/bin/env python3
"""voice-type: local speech-to-text that types wherever your cursor is.

Fully on-device (Moonshine models with built-in VAD). Three activation
modes: a global toggle hotkey, hands-free VAD, or a configurable wake
word ("claude ..."). Configure via ~/.config/voice-type/config.toml.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    VALID_MODEL_ARCHS,
    VALID_MODES,
    load_config,
    write_sample_config,
)

_INSTALL_HINT = (
    "voice-type needs its optional dependencies. Install with:\n"
    '  uv tool install "claude-code-tools[voice]"\n'
    "or run directly:\n"
    '  uvx --from "claude-code-tools[voice]" voice-type'
)


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--mode", choices=VALID_MODES, default=None)
    parser.add_argument(
        "--model-arch", choices=VALID_MODEL_ARCHS, default=None
    )
    parser.add_argument("--language", default=None)
    parser.add_argument(
        "--hotkey", default=None, help='e.g. "<ctrl>+;" (pynput syntax)'
    )
    parser.add_argument("--wake-word", default=None)
    parser.add_argument("--stop-phrase", default=None)
    parser.add_argument(
        "--no-sounds",
        action="store_true",
        help="disable activate/deactivate sounds",
    )


def _cmd_run(args: argparse.Namespace) -> int:
    overrides = {
        "mode": args.mode,
        "model_arch": args.model_arch,
        "language": args.language,
        "hotkey": args.hotkey,
        "wake_word": args.wake_word,
        "stop_phrase": args.stop_phrase,
    }
    if args.no_sounds:
        overrides["sounds"] = False
    try:
        cfg = load_config(args.config, overrides)
    except (ValueError, FileNotFoundError) as e:
        print(f"voice-type: {e}", file=sys.stderr)
        return 1
    try:
        from .app import VoiceTypeApp
    except ImportError as e:
        print(f"voice-type: {e}\n\n{_INSTALL_HINT}", file=sys.stderr)
        return 1
    VoiceTypeApp(cfg).run()
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        path = write_sample_config(args.config, force=args.force)
    except FileExistsError as e:
        print(f"voice-type: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


def main() -> int:
    """Entry point for the voice-type CLI."""
    parser = argparse.ArgumentParser(
        prog="voice-type",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  voice-type                     # run with ~/.config/voice-type/config.toml
  voice-type --mode wake         # hands-free with wake word "claude"
  voice-type --hotkey "<ctrl>+;" # custom toggle hotkey
  voice-type init                # write a commented sample config

macOS permissions: grant your terminal Accessibility (to type) and
Microphone access, plus Input Monitoring for the global hotkey
(System Settings > Privacy & Security).
""",
    )
    _add_run_args(parser)
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser(
        "init", help="write a sample config file and exit"
    )
    p_init.add_argument(
        "--config", type=Path, default=None, help="destination path"
    )
    p_init.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )

    args = parser.parse_args()
    if args.command == "init":
        return _cmd_init(args)
    return _cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
