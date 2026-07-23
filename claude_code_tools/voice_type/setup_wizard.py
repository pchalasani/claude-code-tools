"""Interactive setup wizard for voice-type (``voice-type setup``).

Walks a new user through the handful of choices that matter — engine,
activation mode, segmentation, hotkey, wake word, and a few niceties —
explaining each, then writes a valid ``config.toml`` and prints the
command to run. Unlike ``voice-type init`` (which drops a fully
commented sample file to edit by hand), this asks questions.
"""

from __future__ import annotations

from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    VALID_MODEL_ARCHS,
    Config,
)


class _Cancelled(Exception):
    """Raised when the user aborts a prompt (Ctrl-C / Esc)."""


def _ask(prompt):  # noqa: ANN001, ANN202
    """Return a prompt's answer, or raise ``_Cancelled`` if aborted.

    questionary returns ``None`` when a prompt is cancelled; every
    answer flows through here so an abort — at a required OR an optional
    prompt — always cancels the whole wizard instead of being mistaken
    for a "no"/default and writing a config anyway.
    """
    value = prompt.ask()
    if value is None:
        raise _Cancelled
    return value


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {v!r} to TOML")


def _write_config(path: Path, answers: dict) -> None:
    lines = [
        "# voice-type configuration — written by `voice-type setup`.",
        "# Run `voice-type init --force` for a fully commented sample.",
        "",
    ]
    for key, value in answers.items():
        lines.append(f"{key} = {_toml_value(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _ask_hotkey(q, label: str, default: str):  # noqa: ANN001, ANN202
    """Return a hotkey string via keep-default / record / type."""
    how = _ask(q.select(
        f"{label} hotkey:",
        choices=[
            f"Keep default ({default})",
            "Record one now (press the combo)",
            "Type it manually",
        ],
    ))
    if how.startswith("Keep"):
        return default
    if how.startswith("Record"):
        try:
            from .hotkey import record_hotkey

            print("  Press the key combo now (15s)...")
            chord = record_hotkey()
        except ImportError:
            chord = None
        if chord:
            print(f"  recorded: {chord}")
            return chord
        print("  nothing recorded; keeping default.")
        return default
    from .hotkey import parse_hotkey

    def _valid(text: str):  # noqa: ANN202
        try:
            parse_hotkey(text)
            return True
        except ValueError as e:
            return str(e)

    return _ask(q.text(
        'Hotkey (e.g. "<ctrl>+;" or "ctrl+;"):',
        default=default,
        validate=_valid,
    ))


def run_setup(config_path: Path | None = None, force: bool = False) -> int:
    """Run the interactive wizard; returns a process exit code."""
    try:
        import questionary as q
    except ImportError as e:  # pragma: no cover - questionary is a base dep
        print(f"voice-type: {e}; setup needs questionary")
        return 1

    path = config_path or DEFAULT_CONFIG_PATH
    ans: dict = {}
    try:
        if path.exists() and not force:
            if not _ask(
                q.confirm(f"{path} exists — overwrite it?", default=False)
            ):
                print("setup cancelled.")
                return 1

        print("voice-type setup — a few questions, then you're ready.\n")

        engine = _ask(q.select(
            "Transcription engine:",
            choices=[
                q.Choice(
                    "parakeet-mlx — best accuracy + speed (Apple GPU; "
                    "needs the voice-mlx extra)",
                    value="parakeet-mlx",
                ),
                q.Choice(
                    "parakeet — Parakeet on CPU (needs voice-parakeet)",
                    value="parakeet",
                ),
                q.Choice(
                    "moonshine — small streaming models (needs voice)",
                    value="moonshine",
                ),
            ],
        ))
        ans["engine"] = engine
        is_parakeet = engine in ("parakeet", "parakeet-mlx")

        if engine == "parakeet":
            ans["parakeet_model"] = _ask(q.select(
                "Parakeet build:",
                choices=[
                    q.Choice("v3-int8 — multilingual, ~490 MB", "v3-int8"),
                    q.Choice(
                        "v2-fp16 — English, higher precision", "v2-fp16"
                    ),
                ],
            ))
        elif engine == "moonshine":
            ans["model_arch"] = _ask(q.select(
                "Moonshine model:",
                choices=list(VALID_MODEL_ARCHS),
                default="medium-streaming",
            ))

        mode = _ask(q.select(
            "How should dictation activate?",
            choices=[
                q.Choice("toggle — a hotkey starts/stops", "toggle"),
                q.Choice("wake — say a wake word (hands-free)", "wake"),
                q.Choice("vad — always on while running", "vad"),
            ],
        ))
        ans["mode"] = mode

        if mode == "toggle" and is_parakeet:
            ans["segmentation"] = _ask(q.select(
                "When should the text appear?",
                choices=[
                    q.Choice(
                        "hold — whole take on toggle-off (most accurate)",
                        "hold",
                    ),
                    q.Choice("vad — each utterance when you pause", "vad"),
                ],
            ))

        ans["hotkey"] = _ask_hotkey(q, "Toggle-recording", "<ctrl>+;")

        if mode == "wake":
            word = _ask(q.text("Wake word:", default="claude"))
            ans["wake_word"] = word.strip() or "claude"
            aliases = _ask(q.text(
                "Wake-word aliases the model mishears "
                "(comma-separated, optional):",
                default="",
            ))
            if aliases.strip():
                ans["wake_word_aliases"] = [
                    a.strip() for a in aliases.split(",") if a.strip()
                ]

        if _ask(q.confirm(
            "Configure extras (sounds, clipboard rescue, ghost)?",
            default=False,
        )):
            ans["sounds"] = _ask(
                q.confirm("Play start/stop chimes?", default=True)
            )
            if _ask(q.confirm(
                "Add a hotkey to re-type the last transcript "
                "(wrong-window rescue)?",
                default=False,
            )):
                ans["paste_hotkey"] = _ask_hotkey(
                    q, "Paste-again", "<cmd>+<ctrl>+v"
                )
                ans["copy_to_clipboard"] = True
            ans["overlay"] = _ask(q.confirm(
                "Show the floating ghost while recording?", default=True
            ))
    except _Cancelled:
        print("\nsetup cancelled — nothing written.")
        return 1

    # Validate the combination before writing.
    try:
        Config(**ans).validate()
    except (ValueError, TypeError) as e:
        print(f"\nvoice-type: those choices don't combine ({e}).")
        return 1

    _write_config(path, ans)
    print(f"\n✓ wrote {path}")
    print("\nStart dictating with:\n  voice-type")
    return 0
