"""Interactive setup wizard for voxtype (``voxtype setup``).

Walks a new user through the handful of choices that matter — engine,
activation mode, segmentation, hotkey, wake word, and a few niceties —
explaining each, then writes a valid ``config.toml`` and prints the
command to run. Unlike ``voxtype init`` (which drops a fully
commented sample file to edit by hand), this asks questions.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    VALID_MODEL_ARCHS,
    Config,
)


class _Cancelled(Exception):
    """Raised when the user aborts a prompt (Ctrl-C).

    questionary 2.x turns Ctrl-C (and Ctrl-Q) into the ``None`` answer
    that ``_ask`` maps to this exception; Escape is not a cancellation
    key inside questionary prompts, so it is not advertised here.
    """


def _ask(prompt):  # noqa: ANN001, ANN202
    """Return a prompt's answer, or raise ``_Cancelled`` if aborted.

    questionary returns ``None`` when a prompt is cancelled (Ctrl-C);
    every answer flows through here so an abort — at a required OR an
    optional prompt — always cancels the whole wizard instead of being
    mistaken for a "no"/default and writing a config anyway.
    """
    value = prompt.ask()
    if value is None:
        raise _Cancelled
    return value


# TOML basic-string escapes required by the spec; every other control
# character (U+0000–U+001F and U+007F) is emitted as a \uXXXX escape so
# a prompt answer with a newline/tab/etc. never produces invalid TOML.
_TOML_STR_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_string(s: str) -> str:
    out: list[str] = []
    for ch in s:
        escaped = _TOML_STR_ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return _toml_string(v)
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {v!r} to TOML")


def _atomic_write(path: Path, text: str, overwrite: bool) -> None:
    """Write ``text`` to ``path`` atomically.

    Writes to a temporary file in the destination directory, flushes and
    fsyncs it, then publishes it over the destination so an I/O failure or
    interruption can never truncate or half-write an existing config. The
    temporary file is removed on any failure.

    When ``overwrite`` is true the publish is an unconditional
    ``os.replace``. When it is false — the caller never authorized
    clobbering an existing config — the publish is an atomic no-clobber
    ``os.link``, which raises ``FileExistsError`` if the destination
    appeared (e.g. another process created it) after the wizard's initial
    existence check, so a config is never silently destroyed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".config-", suffix=".toml.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            # Atomic create-or-fail: link succeeds only if path is absent.
            os.link(tmp, path)
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_config(path: Path, answers: dict, overwrite: bool) -> None:
    lines = [
        "# voxtype configuration — written by `voxtype setup`.",
        "# Run `voxtype init --force` for a fully commented sample.",
        "",
    ]
    for key, value in answers.items():
        lines.append(f"{key} = {_toml_value(value)}")
    text = "\n".join(lines) + "\n"
    # Guard: never replace the destination with a document tomllib can't
    # read back (the loader that voxtype itself uses).
    tomllib.loads(text)
    _atomic_write(path, text, overwrite)


def _manual_hotkey(q, default: str):  # noqa: ANN001, ANN202
    """Prompt for a hotkey typed by hand, validated by ``parse_hotkey``."""
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
            # Recording needs the optional voice dependency (pynput). Say
            # so — rather than the ambiguous "nothing recorded" — and fall
            # through to manual entry instead of silently keeping default.
            print(
                "  recording needs the voice dependency (pynput); "
                "type the hotkey manually instead."
            )
            return _manual_hotkey(q, default)
        except KeyboardInterrupt:
            # Ctrl-C during the record window cancels the whole wizard,
            # matching every other prompt — nothing gets written.
            raise _Cancelled
        if not chord:
            print("  nothing recorded; keeping default.")
            return default
        # A recorded chord can contain keys the hotkey listener can't
        # bind (e.g. <caps_lock>, media keys). Validate it the same way
        # a manually typed one is validated, so the wizard never writes
        # a config that later fails when the listeners start.
        from .hotkey import parse_hotkey

        try:
            parse_hotkey(chord)
        except ValueError as e:
            print(
                f"  recorded {chord!r} can't be used as a hotkey ({e}); "
                "type one manually instead."
            )
        else:
            print(f"  recorded: {chord}")
            return chord
        # fall through to manual entry (which retries / keeps the default)
    return _manual_hotkey(q, default)


def run_setup(config_path: Path | None = None, force: bool = False) -> int:
    """Run the interactive wizard; returns a process exit code."""
    try:
        import questionary as q
    except ImportError as e:  # pragma: no cover - questionary is a base dep
        print(f"voxtype: {e}; setup needs questionary")
        return 1

    path = config_path or DEFAULT_CONFIG_PATH
    ans: dict = {}
    # Whether clobbering an existing config was authorized: --force, or the
    # user confirming the overwrite prompt below. If neither, the final
    # write uses an atomic no-clobber publish so a config created by another
    # process while the wizard runs is never silently overwritten.
    overwrite_ok = force
    try:
        if path.exists() and not force:
            if not _ask(
                q.confirm(f"{path} exists — overwrite it?", default=False)
            ):
                print("setup cancelled.")
                return 1
            overwrite_ok = True

        print("voxtype setup — a few questions, then you're ready.\n")

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
        print(f"\nvoxtype: those choices don't combine ({e}).")
        return 1

    try:
        _write_config(path, ans, overwrite_ok)
    except FileExistsError:
        # A config appeared after the initial existence check and the user
        # never authorized an overwrite; refuse rather than clobber it.
        print(
            f"\nvoxtype: {path} was created while setup was running; "
            "not overwriting it. Re-run with --force to replace it."
        )
        return 1
    print(f"\n✓ wrote {path}")
    print("\nStart dictating with:\n  voxtype")
    return 0
