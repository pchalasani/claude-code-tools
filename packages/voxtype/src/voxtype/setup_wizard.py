"""Interactive setup wizard for voxtype (``voxtype setup``).

Walks through the common choices first (how you dictate, the hotkey,
cleanup), then advanced ones only if asked, explaining each. It edits
the existing ``config.toml`` in place: current values are the defaults,
comments and other settings are kept, and a summary of the changes is
shown before saving. ``voxtype init`` instead drops a fully commented
sample file to edit by hand.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import tomllib
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    VALID_MODEL_ARCHS,
    Config,
    default_engine,
    sample_config,
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
    """Return a hotkey string via keep-current / record / type."""
    keep = f"Keep current ({default})" if default else "Keep it off"
    how = _ask(q.select(
        f"{label} hotkey:",
        choices=[keep, "Record one now (press the combo)",
                 "Type it manually"],
        default=keep,
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


def _section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


def _number(q, message: str, field: str, default: float):  # noqa: ANN001, ANN202
    """Ask for config number ``field``, checked by the config's rules."""
    from dataclasses import fields

    kind = {f.name: f.type for f in fields(Config)}[field]
    integer = kind in (int, "int")

    def convert(text: str) -> float:
        value = float(text)
        if integer:
            if not value.is_integer():
                raise ValueError("enter a whole number")
            return int(value)
        return value

    def _valid(text: str):  # noqa: ANN202
        try:
            Config(**{field: convert(text)}).validate()
        except ValueError as e:
            return str(e) or "enter a number"
        return True

    shown = (
        str(int(default))
        if integer and float(default).is_integer()
        else repr(float(default))
    )
    return convert(_ask(q.text(message, default=shown, validate=_valid)))


def _ask_basics(q, cur: Config, pinned: dict, ans: dict) -> None:  # noqa: ANN001
    """Mode, startup question, hotkey, wake word."""
    from .mode_menu import chord_label

    _section("How you dictate")
    key = chord_label(cur.hotkey)
    ans["mode"] = _ask(q.select(
        "How do you usually want to dictate?",
        choices=[
            q.Choice(
                f"Toggle: press {key} to start recording, {key} to stop",
                "toggle",
            ),
            q.Choice(
                'Always-on: just talk; end with "send it" to send', "vad"
            ),
            q.Choice(
                f'Wake word: say "{cur.wake_word}" to start dictating',
                "wake",
            ),
        ],
        default=cur.mode,
    ))
    if ans["mode"] != "toggle" and pinned.get("segmentation") == "hold":
        # "hold" (whole-take) works only in toggle mode.
        ans["segmentation"] = "vad"
    if ans["mode"] != "wake":
        ans["ask_mode"] = _ask(q.confirm(
            "When you run plain `voxtype`, ask toggle or always-on each "
            "time?",
            default=cur.ask_mode,
        ))
    ans["hotkey"] = _ask_hotkey(q, "Start/stop recording", cur.hotkey)
    if ans["mode"] == "wake":
        word = _ask(q.text("Wake word:", default=cur.wake_word))
        ans["wake_word"] = word.strip() or cur.wake_word
        aliases = _ask(q.text(
            "Ways the transcriber mishears it (comma-separated, optional):",
            default=", ".join(cur.wake_word_aliases),
        ))
        ans["wake_word_aliases"] = [
            a.strip() for a in aliases.split(",") if a.strip()
        ]


def _ask_cleanup(q, cur: Config, ans: dict) -> None:  # noqa: ANN001
    """LLM cleanup: off, Cerebras, or local S1-mini."""
    _section("Cleanup")
    choice = _ask(q.select(
        "Clean up what you dictate with an LLM (punctuation, "
        "self-corrections)?",
        choices=[
            q.Choice("Off: type exactly what was heard", "off"),
            q.Choice(
                "Cerebras: cloud, about 0.3 s, also adds paragraphs and "
                "lists (sends your words to api.cerebras.ai)",
                "cerebras",
            ),
            q.Choice(
                "S1-mini: runs on this Mac, private; punctuation and "
                "capitals only (~462 MiB download)",
                "s1-mini",
            ),
        ],
        default=cur.post_process_provider if cur.post_process else "off",
    ))
    ans["post_process"] = choice != "off"
    if choice == "off":
        return
    ans["post_process_provider"] = choice
    if choice == "cerebras":
        ans["post_process_format"] = _ask(q.select(
            "Formatting:",
            choices=[
                q.Choice(
                    "auto: paragraphs and lists when they help", "auto"
                ),
                q.Choice("paragraphs: paragraph breaks, no lists",
                         "paragraphs"),
                q.Choice("minimal: fix punctuation only", "minimal"),
            ],
            default=cur.post_process_format,
        ))
        from .postprocess_cerebras import KEY_ENV, find_key, key_files

        if find_key(lambda _msg: None):
            print("  Cerebras API key: found.")
        else:
            print(
                f"  Cerebras API key: not found. Put {KEY_ENV}=... in "
                f"{key_files()[0]} (chmod 600); until then your words are "
                "typed without cleanup."
            )
    else:
        ans["post_process_style"] = _ask(q.select(
            "Cleanup style:",
            choices=[
                q.Choice(
                    "semi-formal: sentences, capitals, full stops",
                    "semi-formal",
                ),
                q.Choice("formal: also expands contractions", "formal"),
                q.Choice("semi-casual: light punctuation", "semi-casual"),
                q.Choice("casual: lowercase, minimal punctuation",
                         "casual"),
            ],
            default=cur.post_process_style,
        ))
        if shutil.which("llama-server") is None:
            print(
                "  note: llama-server not found; install it with "
                "`brew install llama.cpp` (until then your words are "
                "typed without cleanup)."
            )


def _ask_advanced(q, cur: Config, ans: dict) -> None:  # noqa: ANN001
    """Engine, hands-free safeguards, time limit, keys, niceties."""
    _section("Engine")
    engine = _ask(q.select(
        "Transcription engine:",
        choices=[
            q.Choice(
                "parakeet-mlx: best accuracy and speed (Apple Silicon GPU)",
                "parakeet-mlx",
            ),
            q.Choice("parakeet: Parakeet on the CPU (works everywhere)",
                     "parakeet"),
            q.Choice(
                "moonshine: small streaming models (needs the "
                "voxtype[moonshine] extra)",
                "moonshine",
            ),
        ],
        default=cur.engine or default_engine(),
    ))
    ans["engine"] = engine
    if engine == "parakeet":
        ans["parakeet_model"] = _ask(q.select(
            "Parakeet build:",
            choices=[
                q.Choice("v3-int8: multilingual, ~490 MB", "v3-int8"),
                q.Choice("v2-fp16: English, higher precision", "v2-fp16"),
            ],
            default=cur.parakeet_model,
        ))
    elif engine == "moonshine":
        ans["model_arch"] = _ask(q.select(
            "Moonshine model:",
            choices=list(VALID_MODEL_ARCHS),
            default=cur.model_arch,
        ))
        if ans.get("segmentation", cur.segmentation) == "hold":
            ans["segmentation"] = "vad"  # "hold" needs a parakeet engine
    if ans["mode"] == "toggle" and engine != "moonshine":
        ans["segmentation"] = _ask(q.select(
            "In toggle mode, when should text appear?",
            choices=[
                q.Choice(
                    "hold: all at once when you stop (most accurate)",
                    "hold",
                ),
                q.Choice("vad: piece by piece as you pause", "vad"),
            ],
            default=cur.segmentation,
        ))

    if ans["mode"] == "vad" or ans.get("ask_mode"):
        _section("Always-on safeguards")
        ans["wait_for_submit"] = _ask(q.confirm(
            'Hold what you say until "send it" (instead of typing at '
            "every pause)?",
            default=cur.wait_for_submit,
        ))
        ans["held_gap"] = _number(
            q, "Drop held words after a silence of this many seconds "
            "(0 = never):", "held_gap", cur.held_gap,
        )
        ans["held_max_words"] = _number(
            q, "Type without sending when more than this many words are "
            "held (0 = no limit):", "held_max_words", cur.held_max_words,
        )
        if ans.get("post_process_provider") == "cerebras":
            ans["held_check_words"] = _number(
                q, "Ask Cerebras where your message starts once this many "
                "words are held (0 = never):", "held_check_words",
                cur.held_check_words,
            )

    if ans.get("post_process"):
        _section("Cleanup time limit")
        ans["post_process_timeout"] = _number(
            q, "Seconds to wait for cleanup before typing the raw text "
            "(0 = default: 2 for Cerebras, 4 for S1-mini):",
            "post_process_timeout", cur.post_process_timeout,
        )

    _section("Other keys and extras")
    if _ask(q.confirm(
        "Change the cancel (Esc) and discard (Shift+Esc) keys?",
        default=False,
    )):
        ans["cancel_hotkey"] = _ask_hotkey(
            q, "Cancel a recording", cur.cancel_hotkey
        )
        ans["discard_hotkey"] = _ask_hotkey(
            q, "Discard held text (always-on)", cur.discard_hotkey
        )
    if _ask(q.confirm(
        "Add a key that re-types your last message (wrong-window rescue)?",
        default=bool(cur.paste_hotkey),
    )):
        ans["paste_hotkey"] = _ask_hotkey(
            q, "Re-type last message", cur.paste_hotkey or "<cmd>+<ctrl>+v"
        )
    else:
        ans["paste_hotkey"] = ""
    ans["copy_to_clipboard"] = _ask(q.confirm(
        "Also copy each message to the clipboard?",
        default=cur.copy_to_clipboard,
    ))
    ans["sounds"] = _ask(
        q.confirm("Play start/stop chimes?", default=cur.sounds)
    )
    ans["overlay"] = _ask(q.confirm(
        "Show the floating ghost while recording?", default=cur.overlay
    ))


def _changes(cur: Config, ans: dict) -> dict:
    """The answers that differ from the current settings."""
    return {k: v for k, v in ans.items() if getattr(cur, k) != v}


def _render(path: Path, base: str | None, changes: dict) -> str:
    """The config text with ``changes`` applied, comments kept."""
    import tomlkit

    doc = tomlkit.parse(base if base is not None else sample_config())
    for key, value in changes.items():
        doc[key] = value
    text = tomlkit.dumps(doc)
    data = tomllib.loads(text)  # never write what voxtype can't read
    Config(**data).validate()
    return text


def run_setup(config_path: Path | None = None, force: bool = False) -> int:
    """Run the interactive wizard; returns a process exit code.

    Edits the config in place: your current values are the defaults,
    and comments and other settings are kept. ``force`` starts from the
    built-in defaults instead and replaces the file.
    """
    try:
        import questionary as q
    except ImportError as e:  # pragma: no cover - questionary is a base dep
        print(f"voxtype: {e}; setup needs questionary")
        return 1

    path = config_path or DEFAULT_CONFIG_PATH
    base = None
    if path.exists() and not force:
        base = path.read_text(encoding="utf-8")
    try:
        pinned = tomllib.loads(base) if base is not None else {}
        cur = Config(**pinned)
        cur.validate()
    except (ValueError, TypeError) as e:
        print(
            f"voxtype: can't read {path} ({e}). Fix it, or run "
            "`voxtype setup --force` to start from the defaults."
        )
        return 1

    print(
        "voxtype setup: the common settings first; press Enter to keep "
        "what's shown."
    )
    if base is not None:
        print(f"Editing {path} (your comments and other settings stay).")
    ans: dict = {}
    try:
        _ask_basics(q, cur, pinned, ans)
        _ask_cleanup(q, cur, ans)
        print()
        if _ask(q.confirm(
            "Change advanced settings (engine, always-on safeguards, "
            "other keys, sounds)?",
            default=False,
        )):
            _ask_advanced(q, cur, ans)
        changes = _changes(cur, ans)
        if not changes and base is not None:
            print("\nNo changes; your config is as it was.")
            return 0
        _section("Summary")
        for key, value in changes.items():
            print(f"  {key}: {getattr(cur, key)!r} → {value!r}")
        if not changes:
            print("  (all defaults)")
        try:
            text = _render(path, base, changes)
        except (ValueError, TypeError) as e:
            print(f"\nvoxtype: those choices don't combine ({e}).")
            return 1
        if not _ask(q.confirm(f"Save to {path}?", default=True)):
            print("Nothing saved.")
            return 1
    except _Cancelled:
        print("\nsetup cancelled; nothing saved.")
        return 1

    now = path.read_text(encoding="utf-8") if path.exists() else None
    if now != base and not (force and base is None):
        print(
            f"\nvoxtype: {path} changed while setup was running; not "
            "saving over it. Run `voxtype setup` again."
        )
        return 1
    try:
        _atomic_write(path, text, overwrite=path.exists())
    except FileExistsError:
        print(
            f"\nvoxtype: {path} was created while setup was running; "
            "not saving over it. Run `voxtype setup` again."
        )
        return 1
    print(f"\n✓ saved {path}")
    print("\nStart dictating with:\n  voxtype")
    return 0
