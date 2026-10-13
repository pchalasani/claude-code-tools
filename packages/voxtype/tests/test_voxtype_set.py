"""Tests for ``--set KEY=VALUE`` config overrides."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from voxtype.cli import main
from voxtype.config import load_config, parse_set_overrides


def test_hyphen_and_underscore_keys() -> None:
    got = parse_set_overrides(
        ["post-process-provider=s1-mini", "post_process_format=minimal"]
    )
    assert got == {
        "post_process_provider": "s1-mini",
        "post_process_format": "minimal",
    }


def test_values_take_the_field_type() -> None:
    got = parse_set_overrides(
        [
            "sounds=off",
            "post-process=TRUE",
            "parakeet-threads=8",
            "overlay-opacity=0.5",
            "submit-phrases=go, send it",
            'wake-word-aliases=["clawed", "cloud"]',
            "double-tap-key=",
        ]
    )
    assert got == {
        "sounds": False,
        "post_process": True,
        "parakeet_threads": 8,
        "overlay_opacity": 0.5,
        "submit_phrases": ["go", "send it"],
        "wake_word_aliases": ["clawed", "cloud"],
        "double_tap_key": "",
    }


def test_value_may_contain_equals_and_later_item_wins() -> None:
    got = parse_set_overrides(["hotkey=<ctrl>+=", "hotkey=<ctrl>+;"])
    assert got == {"hotkey": "<ctrl>+;"}
    assert parse_set_overrides(["hotkey=a=b"]) == {"hotkey": "a=b"}


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ("sounds", "KEY=VALUE"),
        ("post-proces=true", "did you mean post_process"),
        ("sounds=maybe", "true or false"),
        ("parakeet-threads=many", "expected a number"),
        ("submit-phrases=[1,", "bad list"),
    ],
)
def test_bad_items_raise(item: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_set_overrides([item])


def test_overrides_beat_the_config_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('post_process_provider = "cerebras"\nsounds = true\n')
    cfg = load_config(
        cfg_path,
        parse_set_overrides(["post-process-provider=s1-mini", "sounds=no"]),
    )
    assert cfg.post_process_provider == "s1-mini"
    assert cfg.sounds is False


def test_set_values_are_validated(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")
    with pytest.raises(ValueError, match="post_process_provider"):
        load_config(
            cfg_path, parse_set_overrides(["post-process-provider=nope"])
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["voxtype", "--set", "bogus=1"],
        ["voxtype", "post-process", "--set", "sounds=maybe", "hi"],
    ],
)
def test_cli_reports_bad_set_and_exits_1(
    argv: list[str], capsys, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 1
    assert "voxtype: --set" in capsys.readouterr().err
