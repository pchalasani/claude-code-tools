"""Keep the audio of the last few recorded pieces, for diagnosis.

Off unless ``save_audio`` is set. When words go missing from a
transcript, the saved audio shows whether they were recorded at all
(speech detection started late) or recorded but not transcribed.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

#: Where the audio is kept: one wav per piece, plus ``index.txt``.
AUDIO_DIR = Path.home() / ".cache" / "voxtype" / "audio"

Transcribe = Callable[[np.ndarray, int], str]


class AudioLog:
    """Saves each transcribed piece as a wav, keeping the newest
    ``keep``, and lists them with their text in ``index.txt``."""

    def __init__(
        self,
        keep: int,
        report: Callable[[str], None],
        folder: Path = AUDIO_DIR,
    ) -> None:
        self.keep = keep
        self.folder = folder
        self._report = report
        self._lock = threading.Lock()
        self._failed = False

    def wrap(self, transcribe: Transcribe) -> Transcribe:
        """``transcribe``, also saving what it is given and returns."""

        def saving(samples: np.ndarray, sample_rate: int) -> str:
            text = transcribe(samples, sample_rate)
            # All-zero audio is the engine's own warm-up or keepalive.
            if np.size(samples) and np.abs(samples).max() > 0:
                self.save(samples, sample_rate, text)
            return text

        return saving

    def save(self, samples: np.ndarray, sample_rate: int, text: str) -> None:
        """Write one piece; a failure is reported once, never raised."""
        now = time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        name = f"{stamp}-{int(now * 1000) % 1000:03d}.wav"
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        try:
            with self._lock:
                self.folder.mkdir(parents=True, exist_ok=True)
                with wave.open(str(self.folder / name), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(sample_rate)
                    w.writeframes(pcm.tobytes())
                self._prune_and_index(name, text)
        except OSError as e:
            if not self._failed:
                self._failed = True
                self._report(f"could not save audio to {self.folder}: {e}")

    def _prune_and_index(self, name: str, text: str) -> None:
        """Drop all but the newest ``keep`` wavs; rewrite the index."""
        index = self.folder / "index.txt"
        texts: dict[str, str] = {}
        if index.exists():
            for line in index.read_text(encoding="utf-8").splitlines():
                wav, _, said = line.partition("\t")
                texts[wav] = said
        texts[name] = " ".join(text.split()) or "(no text)"
        wavs = sorted(p.name for p in self.folder.glob("*.wav"))
        for old in wavs[: max(0, len(wavs) - self.keep)]:
            (self.folder / old).unlink(missing_ok=True)
        kept = wavs[-self.keep:]
        index.write_text(
            "".join(f"{w}\t{texts.get(w, '')}\n" for w in kept),
            encoding="utf-8",
        )
