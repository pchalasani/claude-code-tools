"""Audio from just before a VAD segment, so first sounds are not lost.

Silero VAD starts a segment only once speech is loud enough. A quiet
first consonant, like the "s" of "send it", falls before that point, and
the transcriber then hears "and it" or "end it". Measured with macOS
voices: the segment started 80-94 ms after "send it" began; adding the
200 ms before it turned "Percentage" into "Sendage" and "Okay, sure"
into "Okay show". Real use then lost a soft "Did we" at the start of
a question, so the preroll is 0.5 s; tests showed no harm from that.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

import numpy as np

#: Seconds of audio added before each VAD segment.
PREROLL_SECONDS = 0.5


class PrerollVad:
    """A sherpa-onnx ``VoiceActivityDetector`` whose segments start
    ``preroll`` samples earlier.

    Keeps the audio it is fed, by sample index, for ``keep`` samples
    (the longest segment plus the preroll), and prepends what came just
    before each segment's ``start``. sherpa-onnx counts ``start`` from
    the detector's creation or last ``reset``, so this counts the same
    way. Everything else is passed through to the detector.
    """

    def __init__(self, vad: Any, preroll: int, keep: int) -> None:  # noqa: ANN401
        self._vad = vad
        self._preroll = preroll
        self._keep = keep
        self._chunks: deque[tuple[int, np.ndarray]] = deque()
        self._end = 0  # index just past the last sample fed
        self._kept = 0

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._vad, name)

    def accept_waveform(self, samples: np.ndarray) -> None:
        """Feed ``samples`` to the detector, keeping a copy."""
        self._vad.accept_waveform(samples)
        chunk = np.array(samples, dtype=np.float32, copy=True)
        self._chunks.append((self._end, chunk))
        self._end += chunk.size
        self._kept += chunk.size
        while self._chunks and self._kept - self._chunks[0][1].size >= (
            self._keep
        ):
            self._kept -= self._chunks.popleft()[1].size

    def reset(self) -> None:
        """Reset the detector; sample indices start again at 0."""
        self._vad.reset()
        self._chunks.clear()
        self._end = self._kept = 0

    def before(self, start: int) -> np.ndarray:
        """The kept audio in ``[start - preroll, start)``."""
        lo, parts = start - self._preroll, []
        for at, chunk in self._chunks:
            a, b = max(lo, at), min(start, at + chunk.size)
            if a < b:
                parts.append(chunk[a - at:b - at])
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts)

    @property
    def front(self) -> Any:  # noqa: ANN401
        """The first finished segment, with the audio before it added.

        A copy: the detector's own ``front`` is invalid after ``pop``.
        """
        front = self._vad.front
        if front is None:
            return None
        raw = getattr(front, "samples", None)
        start = getattr(front, "start", None)
        if raw is None or not isinstance(start, int):
            return front
        samples = np.array(raw, dtype=np.float32, copy=True)
        if samples.ndim != 1:
            return front
        pad = self.before(start)
        return SimpleNamespace(
            start=start - pad.size, samples=np.concatenate([pad, samples])
        )


def with_preroll(
    vad: Any, sample_rate: int, longest: float  # noqa: ANN401
) -> PrerollVad:
    """Wrap ``vad`` to add ``PREROLL_SECONDS`` before each segment.

    ``longest`` is the longest segment in seconds; a few more are kept
    for the time between a segment ending and its being read.
    """
    preroll = int(PREROLL_SECONDS * sample_rate)
    keep = int((longest + 5) * sample_rate) + preroll
    return PrerollVad(vad, preroll, keep)
