"""The startup question: toggle or always-on?

Plain ``voxtype`` (no arguments) in a terminal asks which way to dictate
before starting, explaining both modes in plain words. The configured
``mode`` is the default answer, so Enter starts as usual.
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Callable, Sequence
from typing import Any

from .config import Config

_KEY_NAMES = {
    "ctrl": "Ctrl",
    "shift": "Shift",
    "alt": "Option",
    "cmd": "Cmd",
    "esc": "Esc",
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
}

TOGGLE = "toggle"
ALWAYS_ON = "always-on"

# Config overrides for each answer. Always-on pins "vad" segmentation:
# a config that pins "hold" (whole-take, toggle only) would be invalid.
OVERRIDES: dict[str, dict[str, Any]] = {
    TOGGLE: {"mode": "toggle"},
    ALWAYS_ON: {"mode": "vad", "segmentation": "vad"},
}


def chord_label(chord: str) -> str:
    """Readable form of a hotkey chord: ``"<ctrl>+;"`` -> ``"Ctrl+;"``."""
    parts = []
    for raw in chord.split("+"):
        key = raw.strip().strip("<>")
        if not key:
            return chord
        label = _KEY_NAMES.get(key.lower())
        if label is None:
            label = key.upper() if len(key) == 1 else key.capitalize()
        parts.append(label)
    return "+".join(parts)


def should_ask(argv: Sequence[str], cfg: Config, interactive: bool) -> bool:
    """Whether to ask: plain ``voxtype`` in a terminal, not turned off.

    Args:
        argv: Command-line arguments after the program name.
        cfg: The loaded config.
        interactive: Whether stdin and stdout are terminals.

    Returns:
        True when the startup question should be shown.
    """
    return (
        not argv and interactive and cfg.ask_mode and cfg.mode != "wake"
    )


def _pick(phrases: list[str], preferred: str) -> str:
    """``preferred`` if configured, else the first phrase, else ""."""
    if preferred in phrases:
        return preferred
    return phrases[0] if phrases else ""


def explanations(cfg: Config) -> dict[str, str]:
    """Plain-words description of each mode, using this config's keys
    and phrases."""
    key = chord_label(cfg.hotkey)
    send = _pick(cfg.submit_suffixes, "send it")
    show = _pick(cfg.show_suffixes, "ok show")
    toggle = [
        f"Press {key} to start recording, and {key} again to stop.",
        "Your words are typed where your cursor is; press Enter to send "
        + (f'them (or end with "{send}" to send them for you).'
           if send else "them."),
    ]
    if cfg.cancel_hotkey:
        toggle.append(
            f"{chord_label(cfg.cancel_hotkey)} throws away a recording "
            "in progress."
        )
    always = [
        "voxtype listens all the time: just talk, and pause whenever "
        "you like.",
    ]
    if not cfg.wait_for_submit:
        always.append("Each piece is typed when you pause.")
        if send:
            always.append(f'End with "{send}" to also press Enter.')
    elif send:
        always.append(
            f'Nothing is typed until you end with "{send}"; then it is '
            "typed and sent."
        )
        if show:
            always.append(
                f'End with "{show}" instead to type it without sending, '
                "so you can check it first."
            )
    else:
        always.append(
            f"Nothing is typed until you press {key}, which types it."
        )
    if cfg.discard_hotkey:
        always.append(
            f"{chord_label(cfg.discard_hotkey)} throws away what you've "
            "said so far."
        )
    always.append(f"{key} pauses and resumes listening.")
    return {TOGGLE: "\n".join(toggle), ALWAYS_ON: "\n".join(always)}


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def render(cfg: Config) -> str:
    """The text printed above the question."""
    text = explanations(cfg)
    indent = "    "

    def block(title: str, body: str) -> str:
        lines = "\n".join(
            textwrap.fill(
                line, width=72, initial_indent=indent,
                subsequent_indent=indent,
            )
            for line in body.splitlines()
        )
        return f"  {_bold(title)}\n{lines}"

    return "\n".join([
        "",
        _bold("voxtype: how do you want to dictate?"),
        "",
        block("Toggle", text[TOGGLE]),
        "",
        block("Always-on (hands-free)", text[ALWAYS_ON]),
        "",
    ])


def _questionary_select(default: str) -> str | None:
    import questionary as q

    return q.select(
        "Start in:",
        choices=[
            q.Choice("Toggle", value=TOGGLE),
            q.Choice("Always-on", value=ALWAYS_ON),
        ],
        default=default,
        instruction="(arrow keys, then Enter)",
    ).ask()


def ask(
    cfg: Config,
    select: Callable[[str], str | None] = _questionary_select,
) -> dict[str, Any] | None:
    """Show the question; return config overrides, or None if cancelled.

    Args:
        cfg: The loaded config; its ``mode`` is the default answer.
        select: Asks the question given the default answer; returns the
            answer, or None on Ctrl-C.

    Returns:
        Config overrides for the chosen mode, or None if cancelled.
    """
    print(render(cfg))
    default = ALWAYS_ON if cfg.mode == "vad" else TOGGLE
    choice = select(default)
    if choice is None:
        return None
    print("  (To start without asking, set ask_mode = false in the config.)\n")
    return OVERRIDES[choice]
