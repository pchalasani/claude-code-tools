"""Shifted characters are typed as Shift plus their real key.

Rex 1696 reads a key event's key code, and pynput sends characters it cannot
map directly (capitals, "?", "!") as key code 0, the A key, so Rex inserted
"a" for them. The Typist now presses Shift with the character's own key.
"""

import sys

import pytest

from voxtype.inject import Typist, _shifted_keycodes, plan_keystrokes

US_SHIFTED = {"I": 34, "K": 40, "?": 44, "!": 18}


def test_plan_splits_shifted_characters_out() -> None:
    assert plan_keystrokes("I think, okay? Yes!", US_SHIFTED | {"Y": 16}) == [
        ("shift", 34), ("text", " think, okay"), ("shift", 44),
        ("text", " "), ("shift", 16), ("text", "es"), ("shift", 18),
    ]


def test_plan_without_shifted_map_is_one_run() -> None:
    assert plan_keystrokes("I think?", {}) == [("text", "I think?")]
    assert plan_keystrokes("", US_SHIFTED) == []


class _Keys:
    """Records what the Typist sends to the keyboard controller."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.held: list[object] = []

    def type(self, text: str) -> None:
        self.events.append(f"type:{text}")

    def tap(self, key: object) -> None:
        mods = "+".join(str(k) for k in self.held)
        self.events.append(f"tap:{mods + '+' if mods else ''}{key}")

    def pressed(self, key: object):  # noqa: ANN201
        keys = self

        class _Ctx:
            def __enter__(self) -> None:
                keys.held.append(key)

            def __exit__(self, *a: object) -> None:
                keys.held.pop()

        return _Ctx()


def test_typist_holds_shift_for_shifted_characters() -> None:
    pytest.importorskip("pynput")
    from pynput.keyboard import Key, KeyCode

    typist = Typist()
    typist._shifted_lookup = lambda _kb: dict(US_SHIFTED)
    keys = _Keys()
    typist._keyboard = keys
    typist.type_text("Ok?\nI")
    assert keys.events == [
        "type:Ok",  # O is not in this map, so it stays plain text
        f"tap:{Key.shift}+{KeyCode.from_vk(44)}",
        f"tap:{Key.shift}+{Key.enter}",
        f"tap:{Key.shift}+{KeyCode.from_vk(34)}",
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keyboard layout")
def test_real_layout_map_covers_capitals_and_symbols() -> None:
    pytest.importorskip("pynput")
    from pynput.keyboard import Controller

    shifted = _shifted_keycodes(Controller())
    for ch in "IKOQY?!":
        assert ch in shifted, ch
    assert "a" not in shifted and " " not in shifted  # plain keys untouched


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keyboard layout")
def test_dead_keys_are_never_pressed() -> None:
    pytest.importorskip("pynput")
    from pynput._util.darwin import keycode_context
    from pynput.keyboard import Controller

    from voxtype.inject import _is_dead_key

    shift = 0x0200 >> 8
    with keycode_context() as context:
        for ch, vk in _shifted_keycodes(Controller()).items():
            assert not _is_dead_key(context, vk, shift), ch


def test_lookup_runs_on_every_take() -> None:
    pytest.importorskip("pynput")
    calls = []
    typist = Typist()
    typist._keyboard = _Keys()
    typist._shifted_lookup = lambda _kb: calls.append(1) or {}
    typist.type_text("one")
    typist.type_text("two")
    assert len(calls) == 2


def test_off_macos_the_map_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _shifted_keycodes(object()) == {}
