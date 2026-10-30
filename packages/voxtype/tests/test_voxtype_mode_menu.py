"""The startup question: plain `voxtype` asks toggle or always-on."""

from __future__ import annotations

import pytest

from voxtype import mode_menu
from voxtype.config import Config, load_config


def _cfg(**kw) -> Config:  # noqa: ANN003
    cfg = Config(**kw)
    cfg.validate()
    return cfg


@pytest.mark.parametrize(
    ("chord", "label"),
    [
        ("<ctrl>+;", "Ctrl+;"),
        ("<shift>+<esc>", "Shift+Esc"),
        ("<cmd>+<alt>+v", "Cmd+Option+V"),
        ("<f5>", "F5"),
    ],
)
def test_chord_label(chord: str, label: str) -> None:
    assert mode_menu.chord_label(chord) == label


def test_asks_only_for_plain_interactive_run() -> None:
    cfg = _cfg()
    assert mode_menu.should_ask([], cfg, interactive=True)
    assert not mode_menu.should_ask(["--mode", "vad"], cfg, True)
    assert not mode_menu.should_ask([], cfg, interactive=False)
    assert not mode_menu.should_ask([], _cfg(ask_mode=False), True)
    assert not mode_menu.should_ask([], _cfg(mode="wake"), True)


def test_explanations_use_configured_keys() -> None:
    text = mode_menu.explanations(
        _cfg(hotkey="<cmd>+j", discard_hotkey="<ctrl>+<esc>")
    )
    assert "Press Cmd+J to start recording" in text["toggle"]
    assert "Esc throws away a recording" in text["toggle"]
    assert '"send it"' in text["always-on"]
    assert "Ctrl+Esc throws away" in text["always-on"]


def test_disabled_keys_are_not_mentioned() -> None:
    text = mode_menu.explanations(_cfg(cancel_hotkey="", discard_hotkey=""))
    assert "throws away" not in text["toggle"]
    assert "throws away" not in text["always-on"]


def test_no_hold_back_is_described() -> None:
    text = mode_menu.explanations(_cfg(wait_for_submit=False))
    assert "typed when you pause" in text["always-on"]


@pytest.mark.parametrize(
    ("mode", "default"), [("toggle", "toggle"), ("vad", "always-on")]
)
def test_default_answer_is_configured_mode(mode: str, default: str) -> None:
    seen = []
    mode_menu.ask(_cfg(mode=mode), select=lambda d: seen.append(d) or d)
    assert seen == [default]


def test_cancel_returns_none() -> None:
    assert mode_menu.ask(_cfg(), select=lambda d: None) is None


def test_always_on_overrides_a_pinned_hold(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "config.toml"
    path.write_text('mode = "toggle"\nsegmentation = "hold"\n')
    chosen = mode_menu.ask(load_config(path), select=lambda d: "always-on")
    cfg = load_config(path, chosen)
    assert (cfg.mode, cfg.segmentation) == ("vad", "vad")


def test_toggle_from_vad_config_uses_smart_segmentation(
    tmp_path,  # noqa: ANN001
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('mode = "vad"\nengine = "parakeet"\n')
    chosen = mode_menu.ask(load_config(path), select=lambda d: "toggle")
    cfg = load_config(path, chosen)
    assert (cfg.mode, cfg.segmentation) == ("toggle", "hold")
