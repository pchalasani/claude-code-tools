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
    VALID_ENGINES,
    VALID_MODEL_ARCHS,
    VALID_MODES,
    VALID_PARAKEET_MODELS,
    VALID_SEGMENTATIONS,
    load_config,
    write_sample_config,
)

_INSTALL_HINT = (
    "voice-type needs its optional dependencies. Install with:\n"
    '  uv tool install "claude-code-tools[voice]"\n'
    '(add the parakeet engine with "claude-code-tools[voice,voice-parakeet]")\n'
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
    parser.add_argument(
        "--mode",
        choices=VALID_MODES,
        default=None,
        help="activation: toggle=hotkey, vad=hands-free, "
        "wake=wake word (+hotkey override)",
    )
    parser.add_argument(
        "--engine",
        choices=VALID_ENGINES,
        default=None,
        help="transcription backend; parakeet-mlx (Apple GPU) has the "
        "best accuracy and speed",
    )
    parser.add_argument(
        "--segmentation",
        choices=VALID_SEGMENTATIONS,
        default=None,
        help='"hold" = record from toggle-on to toggle-off, '
        "transcribe the whole take at once",
    )
    parser.add_argument(
        "--parakeet-model",
        choices=VALID_PARAKEET_MODELS,
        default=None,
        help='"v2-fp16" is English-only but higher precision',
    )
    parser.add_argument(
        "--model-arch",
        choices=VALID_MODEL_ARCHS,
        default=None,
        help="moonshine model (moonshine engine only)",
    )
    parser.add_argument(
        "--language", default=None, help='language tag (default "en")'
    )
    parser.add_argument(
        "--hotkey",
        default=None,
        help='toggle chord, e.g. "<ctrl>+;" or "ctrl+;" '
        "(run `voice-type hotkey` to record one)",
    )
    parser.add_argument(
        "--wake-word",
        default=None,
        help='wake phrase for --mode wake (default "claude")',
    )
    parser.add_argument(
        "--stop-phrase",
        default=None,
        help='spoken deactivation phrase (default "stop listening")',
    )
    parser.add_argument(
        "--no-sounds",
        action="store_true",
        help="disable activate/deactivate sounds",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="disable the floating waveform pill",
    )


def _cmd_run(args: argparse.Namespace) -> int:
    overrides = {
        "mode": args.mode,
        "engine": args.engine,
        "segmentation": args.segmentation,
        "parakeet_model": args.parakeet_model,
        "model_arch": args.model_arch,
        "language": args.language,
        "hotkey": args.hotkey,
        "wake_word": args.wake_word,
        "stop_phrase": args.stop_phrase,
    }
    if args.no_sounds:
        overrides["sounds"] = False
    if args.no_overlay:
        overrides["overlay"] = False
    try:
        cfg = load_config(args.config, overrides)
    except (ValueError, FileNotFoundError) as e:
        print(f"voice-type: {e}", file=sys.stderr)
        return 1
    # The optional deps (pynput, moonshine-voice, sherpa-onnx) are
    # imported lazily at construction/run time, so the ImportError guard
    # must cover the whole lifecycle, not just the module import.
    try:
        from .app import VoiceTypeApp

        return VoiceTypeApp(cfg).run()
    except ImportError as e:
        print(f"voice-type: {e}\n\n{_INSTALL_HINT}", file=sys.stderr)
        return 1


def _cmd_hotkey() -> int:
    # record_hotkey() imports pynput lazily, so the ImportError guard
    # must cover the call itself, not just the module import.
    try:
        from .hotkey import record_hotkey

        print(
            "Press the key combo you want as your hotkey (15s timeout)..."
        )
        chord = record_hotkey()
    except ImportError as e:
        print(f"voice-type: {e}\n\n{_INSTALL_HINT}", file=sys.stderr)
        return 1
    if chord is None:
        print("voice-type: no key combo detected", file=sys.stderr)
        return 1
    print(f"\nAdd this to {DEFAULT_CONFIG_PATH}:\n")
    print(f'hotkey = "{chord}"')
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    from .setup_wizard import run_setup

    return run_setup(config_path=args.config, force=args.force)


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        path = write_sample_config(args.config, force=args.force)
    except FileExistsError as e:
        print(f"voice-type: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    print("(tip: `voice-type setup` walks you through it interactively)")
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
  voice-type --engine parakeet-mlx --segmentation hold
                                 # best accuracy: whole-take dictation
  voice-type --mode wake         # hands-free with wake word "claude"
  voice-type setup               # interactive walkthrough -> config
  voice-type init                # write a commented sample config
  voice-type hotkey              # record a chord, print the config line

while recording:
  <hotkey> again  stop and type the take     Esc      cancel (discard)
  say "go"/"over"/"submit" alone -> press Enter
  say "stop listening" -> stop dictating (vad segmentation only)

config file: ~/.config/voice-type/config.toml (all flags + more:
wake_word_aliases, submit_phrases, sounds, copy_to_clipboard,
paste_hotkey, cancel_hotkey, overlay, parakeet_threads, ...)
docs: https://pchalasani.github.io/claude-code-tools/tools/voice-type/

macOS permissions (grant to your terminal, one time): Microphone,
Accessibility (to type), Input Monitoring (global hotkeys).
""",
    )
    _add_run_args(parser)
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser(
        "init", help="write a sample config file and exit"
    )
    p_init.add_argument(
        "--config",
        type=Path,
        # SUPPRESS: without it the subparser's default (None) would
        # clobber a --config given BEFORE the subcommand, silently
        # writing to the default path instead of the requested one.
        default=argparse.SUPPRESS,
        help="destination path",
    )
    p_init.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )
    sub.add_parser(
        "hotkey",
        help="press a key combo and print the matching config line",
    )
    p_setup = sub.add_parser(
        "setup",
        help="interactive walkthrough that writes your config",
    )
    p_setup.add_argument(
        "--config", type=Path, default=None, help="destination path"
    )
    p_setup.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )

    args = parser.parse_args()
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "hotkey":
        return _cmd_hotkey()
    if args.command == "setup":
        return _cmd_setup(args)
    return _cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
