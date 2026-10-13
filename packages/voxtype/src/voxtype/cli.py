#!/usr/bin/env python3
"""voxtype: local speech-to-text that types wherever your cursor is.

Fully on-device (Parakeet on the Apple GPU or CPU, or Moonshine).
Three activation modes: a global toggle hotkey, hands-free VAD, or a
configurable wake word ("claude ..."). Configure via
~/.config/voxtype/config.toml.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_CONFIG_PATH,
    VALID_ENGINES,
    VALID_MODEL_ARCHS,
    VALID_MODES,
    VALID_PARAKEET_MODELS,
    VALID_SEGMENTATIONS,
    load_config,
    parse_set_overrides,
    write_sample_config,
)
from .postprocess import (
    VALID_FORMATS,
    VALID_PROVIDERS,
    VALID_STYLES,
    strip_controls,
)

_INSTALL_HINT = (
    "voxtype needs its dependencies. Install with:\n"
    "  uv tool install voxtype\n"
    "(both parakeet engines are included; the moonshine engine needs\n"
    'the "voxtype[moonshine]" extra)\n'
    "or run directly:\n"
    "  uvx voxtype"
)


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("voxtype")
    except PackageNotFoundError:
        return "unknown"


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        action="version",
        version=f"voxtype {_version()}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="set any config key for this run, as if written in the "
        "config file (repeatable; KEY may use - or _), e.g. "
        "--set post-process-provider=s1-mini",
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
        "(run `voxtype hotkey` to record one)",
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


def _with_set(
    items: list[str] | None, overrides: dict[str, Any]
) -> dict[str, Any]:
    """Merge ``--set`` items under the dedicated-flag ``overrides``.

    Args:
        items: Raw ``KEY=VALUE`` strings from ``--set``.
        overrides: Values from dedicated flags; ``None`` means unset.

    Returns:
        The combined overrides; a dedicated flag beats ``--set``.
    """
    merged = parse_set_overrides(items)
    merged.update({k: v for k, v in overrides.items() if v is not None})
    return merged


def _warn_if_unsupported_platform() -> None:
    """Warn loudly when not on macOS; voxtype is macOS-only.

    The core may partly run elsewhere via the CPU engine, but hotkey
    suppression and the overlay are macOS-only and it is untested — so a
    non-Mac launch gets a clear heads-up rather than a silently degraded
    (hotkey-leaking) experience.
    """
    if sys.platform == "darwin":
        return
    print(
        "voxtype: WARNING — voxtype is supported on macOS only "
        "(Intel and Apple Silicon).\n"
        f"  You are on {sys.platform!r}: the toggle hotkey will NOT be "
        "suppressed (it leaks into\n"
        "  your text), there is no overlay, and this is untested. "
        "Continuing anyway.",
        file=sys.stderr,
    )


def _cmd_run(args: argparse.Namespace) -> int:
    _warn_if_unsupported_platform()
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
        # Dedicated flags win over --set for the same key.
        overrides = _with_set(args.set, overrides)
        cfg = load_config(args.config, overrides)
    except (ValueError, FileNotFoundError) as e:
        print(f"voxtype: {e}", file=sys.stderr)
        return 1
    # The optional deps (pynput, moonshine-voice, sherpa-onnx) are
    # imported lazily at construction/run time, so the ImportError guard
    # must cover the whole lifecycle, not just the module import.
    try:
        from .app import VoiceTypeApp

        return VoiceTypeApp(cfg).run()
    except ImportError as e:
        print(f"voxtype: {e}\n\n{_INSTALL_HINT}", file=sys.stderr)
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
        print(f"voxtype: {e}\n\n{_INSTALL_HINT}", file=sys.stderr)
        return 1
    if chord is None:
        print("voxtype: no key combo detected", file=sys.stderr)
        return 1
    print(f"\nAdd this to {DEFAULT_CONFIG_PATH}:\n")
    print(f'hotkey = "{chord}"')
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    from .setup_wizard import run_setup

    return run_setup(config_path=args.config, force=args.force)


def _cmd_skill() -> int:
    from .skill_install import install_skill

    return install_skill()


def _cmd_post_process(args: argparse.Namespace) -> int:
    """Clean one transcript with the post-processing model and print it.

    Uses the config's post_process_* settings (whether or not
    post_process is on), so it doubles as a way to try styles and to
    download the model ahead of time.
    """
    import time

    from .post_providers import create_post_processor

    overrides = {
        "post_process_style": args.style,
        "post_process_provider": args.provider,
        "post_process_format": args.format,
    }
    try:
        overrides = _with_set(
            (args.set or []) + (args.post_set or []), overrides
        )
        cfg = load_config(args.config, overrides)
    except (OSError, ValueError) as e:
        print(f"voxtype: {e}", file=sys.stderr)
        return 1
    text = " ".join(args.text) if args.text else sys.stdin.read()
    def status(msg: str) -> None:
        print(f"[voxtype] {msg}", file=sys.stderr, flush=True)

    post = create_post_processor(cfg, status, keepalive_minutes=0.0)
    try:
        post.start()
        # Generous: the first run may download the model.
        if not post.wait_ready(timeout=1800):
            return 1
        if not text.strip():
            return 0
        t0 = time.monotonic()
        cleaned = strip_controls(post.clean(text.strip()))
        status(f"total {1000 * (time.monotonic() - t0):.0f} ms")
        print(cleaned)
        return 0
    finally:
        post.stop()


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        path = write_sample_config(args.config, force=args.force)
    except FileExistsError as e:
        print(f"voxtype: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    print("(tip: `voxtype setup` walks you through it interactively)")
    return 0


def main() -> int:
    """Entry point for the voxtype CLI."""
    parser = argparse.ArgumentParser(
        prog="voxtype",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  voxtype                     # run with ~/.config/voxtype/config.toml
  voxtype --engine parakeet-mlx --segmentation hold
                                 # best accuracy: whole-take dictation
  voxtype --mode wake         # hands-free with wake word "claude"
  voxtype --set post-process-provider=s1-mini --set sounds=false
                                 # any config key, for this run only
  voxtype setup               # interactive walkthrough -> config
  voxtype init                # write a commented sample config
  voxtype hotkey              # record a chord, print the config line
  voxtype skill               # install the voxtype skill in Claude + Codex
  voxtype post-process "so um I think"
                                 # try LLM cleanup (post_process) once

while recording:
  <hotkey> again  stop and type the take     Esc      cancel (discard)
  say "go"/"over"/"submit" alone -> press Enter
  say "stop listening" -> stop dictating (vad segmentation only)

config file: ~/.config/voxtype/config.toml (all flags + more:
wake_word_aliases, submit_phrases, sounds, copy_to_clipboard,
paste_hotkey, cancel_hotkey, overlay, parakeet_threads, ...)
--set KEY=VALUE overrides any of these keys for one run; KEY may use
- or _ (e.g. --set post-process-provider=s1-mini --set sounds=false)
docs: https://pchalasani.github.io/claude-code-tools/tools/voxtype/

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
        "--config",
        type=Path,
        # SUPPRESS so a --config given BEFORE the subcommand is not
        # clobbered by the subparser's default (see the init parser).
        default=argparse.SUPPRESS,
        help="destination path",
    )
    p_setup.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )
    sub.add_parser(
        "skill",
        help="install the voxtype skill into Claude Code and Codex "
        "(from the cctools-plugins marketplace)",
    )

    p_post = sub.add_parser(
        "post-process",
        help="clean one transcript with the configured post-processor "
        "(S1-mini downloads its model on first use) and print it",
    )
    p_post.add_argument(
        "text", nargs="*", help="transcript (default: read stdin)"
    )
    p_post.add_argument(
        "--style",
        choices=VALID_STYLES,
        help="override post_process_style for this run (s1-mini)",
    )
    p_post.add_argument(
        "--provider",
        choices=VALID_PROVIDERS,
        help="override post_process_provider for this run",
    )
    p_post.add_argument(
        "--format",
        choices=VALID_FORMATS,
        help="override post_process_format for this run (cerebras)",
    )
    p_post.add_argument(
        "--set",
        dest="post_set",
        action="append",
        metavar="KEY=VALUE",
        help="set any config key for this run (repeatable)",
    )

    args = parser.parse_args()
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "hotkey":
        return _cmd_hotkey()
    if args.command == "setup":
        return _cmd_setup(args)
    if args.command == "skill":
        return _cmd_skill()
    if args.command == "post-process":
        return _cmd_post_process(args)
    return _cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
