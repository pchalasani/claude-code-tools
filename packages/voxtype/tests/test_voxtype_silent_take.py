"""A hold take with no signal is a dead microphone, not a quiet user.

Two real incidents motivated this: Zoom leaving the Shure MV7+ muted at
the CoreAudio level (voxtype read a 1.6e-07 dither floor and decoded
43 s of "speech" to nothing), and a PortAudio stream that went stale
after a lid-open changed the device topology (eight days old; reading
zeros while a fresh stream on the same device read fine). Both showed
up only as "decoded to empty text". These tests pin the fix: measure
the RAW (pre-AGC) peak of every hold take, and when it is below the
dead-mic floor, say so plainly, skip the pointless decode, and reopen
the microphone stream so the stale case self-heals.
"""

from __future__ import annotations

import pytest

from test_voxtype_engines import ScriptedStream, _make_parakeet
from voxtype.config import Config


def _hold_engine(monkeypatch):  # noqa: ANN001, ANN202
    eng, statuses, holder = _make_parakeet(monkeypatch, texts=())
    eng.cfg = Config(mode="toggle", engine="parakeet", segmentation="hold")
    decoded: list[int] = []

    def transcribe(samples, sr):  # noqa: ANN001, ANN202
        decoded.append(len(samples))
        return f"len={len(samples)}"

    eng.transcribe = transcribe  # type: ignore[method-assign]
    return eng, statuses, holder, decoded


def _final_read(eng, np):  # noqa: ANN001, ANN202
    def read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    return read


def test_silent_take_floor_is_between_dead_and_live_levels() -> None:
    """The floor must sit well above a muted/stale stream's dither
    (measured 1.6e-07) and well below a live dynamic mic's room noise
    (measured 2.8e-03 on a Shure MV7+, 8e-03 on a Studio Display)."""
    from voxtype.engine_parakeet import SILENT_TAKE_PEAK

    assert 1.6e-07 * 50 < SILENT_TAKE_PEAK < 2.8e-03 / 10


def test_silent_hold_take_is_reported_skipped_and_stream_reopened(
    monkeypatch,  # noqa: ANN001
) -> None:
    np = pytest.importorskip("numpy")
    eng, statuses, holder, decoded = _hold_engine(monkeypatch)
    dead = np.full((160, 1), 1.6e-07, dtype=np.float32)  # muted MV7+
    sessions = {"n": 0}

    def stop_take():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] == 1:
            # Silent take; the hold-stop must end this session.
            return ScriptedStream([dead, dead, stop_take])
        return ScriptedStream([_final_read(eng, np)])

    holder["factory"] = factory
    eng.request_hold_start()
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    assert decoded == []  # nothing to decode
    assert utterances == []
    assert sessions["n"] == 2  # the microphone stream was reopened
    silent = [s for s in statuses if "silent" in s]
    assert len(silent) == 1
    assert "0.0s" in silent[0] or "0.02s" in silent[0]
    assert "reopen" in silent[0]
    assert "1.6e-07" in silent[0]  # the measured peak, for diagnosis
    # It is a deliberate reopen, not a capture failure: no retry
    # countdown, no backoff message.
    assert not any("retrying" in s for s in statuses)


def test_silent_take_uses_raw_peak_not_agc_output(monkeypatch) -> None:  # noqa: ANN001
    """The AGC amplifies up to 60x, so a dead stream's 1e-06 becomes
    6e-05 in the hold buffer — still dead. The judgment must be made
    on the raw samples, before gain, so a dead mic can never be
    'rescued' into looking live."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder, decoded = _hold_engine(monkeypatch)
    from voxtype.engine_parakeet import SILENT_TAKE_PEAK

    # Raw level just under the floor; after 60x it would be well over.
    raw = SILENT_TAKE_PEAK * 0.5
    assert raw * 60 > SILENT_TAKE_PEAK
    dead = np.full((160, 1), raw, dtype=np.float32)

    def stop_take():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    sessions = {"n": 0}

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] == 1:
            return ScriptedStream([dead] * 30 + [stop_take])  # ~3 s
        return ScriptedStream([_final_read(eng, np)])

    holder["factory"] = factory
    eng.request_hold_start()
    eng._loop(lambda t: None, lambda: None)
    assert decoded == []
    assert any("silent" in s for s in statuses)


def test_live_take_still_decodes_and_peak_resets_per_take(
    monkeypatch,  # noqa: ANN001
) -> None:
    """A normal take is untouched, and the raw-peak tracking starts
    fresh on every hold-start: a loud take followed by a dead one
    must flag the dead one (not inherit the loud peak)."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder, decoded = _hold_engine(monkeypatch)
    live = np.full((160, 1), 0.02, dtype=np.float32)  # quiet speech
    dead = np.full((160, 1), 1e-07, dtype=np.float32)

    def stop_then_start():  # noqa: ANN202
        eng.request_hold_stop()
        eng.request_hold_start()
        return np.zeros((0, 1), dtype=np.float32)

    def stop_take():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    sessions = {"n": 0}

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] == 1:
            return ScriptedStream(
                [live, live, stop_then_start, dead, dead, stop_take]
            )
        return ScriptedStream([_final_read(eng, np)])

    holder["factory"] = factory
    eng.request_hold_start()
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    assert utterances == ["len=320"]  # the live take was delivered
    assert decoded == [320]  # ...and only that one was decoded
    assert sum("silent" in s for s in statuses) == 1
    assert sessions["n"] == 2


def test_silent_reopen_does_not_count_toward_fatal_limit(
    monkeypatch,  # noqa: ANN001
) -> None:
    """A muted mic produces a silent take every time the user tries;
    each reopen is deliberate and must never accumulate into the
    'giving up' fatal error that real capture failures trigger."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder, decoded = _hold_engine(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 3
    dead = np.full((160, 1), 1e-07, dtype=np.float32)

    def stop_take():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    def start_take():  # noqa: ANN202
        eng.request_hold_start()
        return np.zeros((0, 1), dtype=np.float32)

    sessions = {"n": 0}

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] <= 4:  # four silent takes in a row
            return ScriptedStream([start_take, dead, stop_take])
        return ScriptedStream([_final_read(eng, np)])

    holder["factory"] = factory
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is None
    assert sessions["n"] == 5
    assert sum("silent" in s for s in statuses) == 4


def test_silent_take_resets_consecutive_failure_count(monkeypatch) -> None:  # noqa: ANN001
    """Two real failures, then a silent take (audio WAS captured), then
    one more failure: the failures were not consecutive, so with a
    limit of three the engine must not go fatal."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder, _ = _hold_engine(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 3
    dead = np.full((160, 1), 1e-07, dtype=np.float32)

    def start_take():  # noqa: ANN202
        eng.request_hold_start()
        return np.zeros((0, 1), dtype=np.float32)

    def stop_take():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    sessions = {"n": 0}

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] in (1, 2, 4):
            raise RuntimeError("mic gone")  # opens fail: real failures
        if sessions["n"] == 3:
            return ScriptedStream([start_take, dead, stop_take])
        return ScriptedStream([_final_read(eng, np)])

    holder["factory"] = factory
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is None
    assert sessions["n"] == 5


def test_silent_take_ignores_audio_beyond_the_hold_cap(monkeypatch) -> None:  # noqa: ANN001
    """A capped take holds only its first MAX_HOLD_SECONDS; a loud
    sample arriving after the cap is not in the take and must not make
    a silent take look live."""
    np = pytest.importorskip("numpy")
    import voxtype.engine_parakeet as ep

    monkeypatch.setattr(ep, "MAX_HOLD_SECONDS", 0.02)  # 320 samples
    eng, statuses, holder, decoded = _hold_engine(monkeypatch)
    dead = np.full((160, 1), 1e-07, dtype=np.float32)
    loud = np.full((160, 1), 0.5, dtype=np.float32)

    def stop_take():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    sessions = {"n": 0}

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] == 1:
            # 320 silent samples fill the cap; the loud chunk is dropped.
            return ScriptedStream([dead, dead, loud, stop_take])
        return ScriptedStream([_final_read(eng, np)])

    holder["factory"] = factory
    eng.request_hold_start()
    eng._loop(lambda t: None, lambda: None)
    assert decoded == []
    assert any("silent" in s for s in statuses)
    assert any("capped" in s for s in statuses)
