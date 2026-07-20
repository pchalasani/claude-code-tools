"""Tests for the voice-type CLI: config handling and matching logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_tools.voice_type.config import (
    Config,
    load_config,
    sample_config,
    write_sample_config,
)
from claude_code_tools.voice_type.hotkey import parse_hotkey
from claude_code_tools.voice_type.logic import (
    contains_phrase,
    is_exact_phrase,
    normalize_words,
    text_after_wake_word,
)


# -- logic ----------------------------------------------------------------


def test_normalize_words_strips_punctuation_and_case() -> None:
    assert normalize_words("Hello, World!  It's me.") == [
        "hello",
        "world",
        "it's",
        "me",
    ]


def test_contains_phrase_word_boundaries() -> None:
    assert contains_phrase("Claude, open the file", "claude")
    assert contains_phrase("please stop listening now", "stop listening")
    assert not contains_phrase("clauded something", "claude")
    assert not contains_phrase("stop, then keep listening", "stop listening")
    assert not contains_phrase("anything", "")


def test_text_after_wake_word() -> None:
    assert text_after_wake_word("Claude, write hello", "claude") == (
        "write hello"
    )
    assert text_after_wake_word("Hey Claude", "claude") == ""
    assert text_after_wake_word("no wake here", "claude") is None
    assert text_after_wake_word("hey claude bot, go", "claude bot") == "go"


def test_is_exact_phrase() -> None:
    assert is_exact_phrase("Go!", "go")
    assert is_exact_phrase("  Over. ", "over")
    assert not is_exact_phrase("go to the file", "go")
    assert not is_exact_phrase("", "go")
    assert not is_exact_phrase("go", "")


# -- hotkey parsing -------------------------------------------------------


def test_parse_hotkey() -> None:
    assert parse_hotkey("<ctrl>+;") == (frozenset({"ctrl"}), ";")
    assert parse_hotkey("<ctrl>+<alt>+d") == (
        frozenset({"ctrl", "alt"}),
        "d",
    )
    assert parse_hotkey("<cmd>+<shift>+<f5>") == (
        frozenset({"cmd", "shift"}),
        "<f5>",
    )
    assert parse_hotkey("<option>+x") == (frozenset({"alt"}), "x")


@pytest.mark.parametrize(
    "bad", ["<ctrl>", "<ctrl>+a+b", "<ctrl>+ab", "<f5>+a+<f6>", ""]
)
def test_parse_hotkey_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(bad)


# -- config ---------------------------------------------------------------


def test_default_config_is_valid() -> None:
    Config().validate()


def test_load_config_missing_default_uses_defaults(tmp_path: Path) -> None:
    cfg = load_config(None, overrides={"mode": "wake"})
    assert cfg.mode == "wake"
    assert cfg.model_arch == "medium-streaming"


def test_load_config_explicit_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_load_config_roundtrip_and_overrides(tmp_path: Path) -> None:
    path = write_sample_config(tmp_path / "config.toml")
    cfg = load_config(path)
    assert cfg == Config()  # sample documents the defaults

    path.write_text('mode = "vad"\nhotkey = "<ctrl>+<alt>+d"\n')
    cfg = load_config(path, overrides={"hotkey": "<ctrl>+;", "mode": None})
    assert cfg.mode == "vad"  # None override ignored
    assert cfg.hotkey == "<ctrl>+;"  # non-None override wins


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('modee = "toggle"\n')
    with pytest.raises(ValueError, match="unknown config keys"):
        load_config(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "bogus"),
        ("model_arch", "large"),
        ("idle_timeout", -1.0),
    ],
)
def test_validate_rejects_bad_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        Config(**{field: value}).validate()


def test_validate_wake_mode_requires_wake_word() -> None:
    with pytest.raises(ValueError, match="wake_word"):
        Config(mode="wake", wake_word="  ").validate()


def test_write_sample_config_refuses_overwrite(tmp_path: Path) -> None:
    path = write_sample_config(tmp_path / "config.toml")
    with pytest.raises(FileExistsError):
        write_sample_config(path)
    write_sample_config(path, force=True)  # --force succeeds
    assert "voice-type configuration" in sample_config()
