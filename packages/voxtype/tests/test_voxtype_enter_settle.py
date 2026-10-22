"""The real Typist waits briefly between typed text and its Enter."""

import time

from voxtype import inject


class _Keys:
    """Records when keystrokes are sent (stands in for pynput)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, float]] = []

    def type(self, text: str) -> None:
        self.events.append((text, time.monotonic()))

    def tap(self, key: object) -> None:
        self.events.append(("<ENTER>", time.monotonic()))


def _typist() -> inject.Typist:
    t = inject.Typist.__new__(inject.Typist)
    t._keyboard = _Keys()
    t._typed_at = 0.0
    return t


def test_enter_waits_after_typing() -> None:
    t = _typist()
    t.type_text("a long held dictation")
    t.press_enter()
    (_, typed), (_, entered) = t._keyboard.events
    assert entered - typed >= inject._ENTER_SETTLE_S * 0.9


def test_bare_enter_does_not_wait() -> None:
    t = _typist()
    start = time.monotonic()
    t.press_enter()
    assert time.monotonic() - start < 0.1
