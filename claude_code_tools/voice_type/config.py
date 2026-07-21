"""Configuration for voice-type.

Config lives at ``~/.config/voice-type/config.toml`` (TOML, stdlib
``tomllib``). CLI flags override file values; every field has a sensible
default so voice-type runs with no config file at all.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "voice-type" / "config.toml"

VALID_MODES = ("toggle", "vad", "wake")

VALID_ENGINES = ("moonshine", "parakeet", "parakeet-mlx")

VALID_SEGMENTATIONS = ("vad", "hold")

# Parakeet model builds published by k2-fsa (see engine_parakeet.MODELS).
VALID_PARAKEET_MODELS = ("v3-int8", "v2-fp16")

# Mirrors moonshine_voice.ModelArch; kept local so config validation
# doesn't require importing the (optional) moonshine dependency.
VALID_MODEL_ARCHS = (
    "tiny",
    "base",
    "tiny-streaming",
    "base-streaming",
    "small-streaming",
    "medium-streaming",
)


@dataclass
class Config:
    """Runtime settings for voice-type.

    Attributes:
        mode: Activation mode. "toggle" starts paused (hotkey starts
            dictation), "vad" starts dictating immediately, "wake" starts
            passive and activates on the wake word.
        engine: Transcription backend, "moonshine" or "parakeet".
        segmentation: "vad" types each utterance when you pause;
            "hold" records everything between toggle-on and toggle-off
            and transcribes the whole take at once (full context, no
            mid-sentence chopping; parakeet + toggle mode only).
        parakeet_model: Parakeet build: "v3-int8" (multilingual,
            ~490 MB) or "v2-fp16" (English, ~1.1 GB, higher precision).
        parakeet_threads: CPU threads for decoding (4 benchmarks
            fastest for v3-int8 on Apple Silicon; 8 helps v2-fp16).
        strip_fillers: Drop standalone filler words (uh, um, ...) from
            typed text.
        overlay: Show the floating waveform pill while recording
            (macOS only; ignored elsewhere). The pill is hidden while
            paused/passive — its presence IS the recording indicator.
        model_arch: Moonshine model architecture name (moonshine engine
            only).
        language: Language tag understood by Moonshine (e.g. "en").
        hotkey: Global toggle hotkey in pynput syntax, e.g. "<ctrl>+;".
        wake_word: Phrase that activates dictation in "wake" mode.
        wake_word_aliases: Alternate spellings the transcriber may
            produce for the wake word (e.g. "claud", "clawed"); any
            of them activates dictation too.
        stop_phrase: Spoken phrase that deactivates dictation. Only
            heard with VAD segmentation: a "hold" take is raw audio
            until toggle-off (nothing is transcribed mid-take), so in
            hold mode stop with the hotkey, or Esc to cancel.
        submit_phrases: Phrases that press Enter when spoken as an
            entire utterance (e.g. say "go" alone to submit).
        idle_timeout: Seconds of silence after which "wake" mode re-arms.
        trailing_space: Append a space after each typed utterance.
        sounds: Play a system sound on activate/deactivate (macOS).
        sound_start: Sound when recording starts — a macOS system
            sound name (see /System/Library/Sounds) or a file path.
        sound_stop: Sound when recording stops (same forms).
        copy_to_clipboard: Also place each dictation session's text on
            the clipboard (overwrites it per utterance).
        paste_hotkey: Optional global chord (e.g. "<cmd>+<ctrl>+v")
            that types the LAST session's transcript at the cursor —
            rescues dictation that went to the wrong window. Empty
            disables.
        cancel_hotkey: Chord that cancels a recording in progress,
            discarding everything (default Escape). Only intercepted
            WHILE recording; otherwise the key passes through to the
            focused app. Empty disables.
    """

    mode: str = "toggle"
    engine: str = "moonshine"
    segmentation: str = "vad"
    parakeet_model: str = "v3-int8"
    parakeet_threads: int = 4
    mlx_model: str = "mlx-community/parakeet-tdt-0.6b-v3"
    strip_fillers: bool = True
    overlay: bool = True
    model_arch: str = "medium-streaming"
    language: str = "en"
    hotkey: str = "<ctrl>+;"
    wake_word: str = "claude"
    wake_word_aliases: list[str] = field(default_factory=list)
    stop_phrase: str = "stop listening"
    submit_phrases: list[str] = field(
        default_factory=lambda: ["over", "go", "submit"]
    )
    idle_timeout: float = 20.0
    trailing_space: bool = True
    sounds: bool = True
    sound_start: str = "Glass"
    sound_stop: str = "Bottle"
    copy_to_clipboard: bool = False
    paste_hotkey: str = ""
    cancel_hotkey: str = "<esc>"

    def validate(self) -> None:
        """Raise ``ValueError`` if any field has an invalid type or value.

        Every field is type-checked before it is dereferenced, so junk
        values (None, wrong types) raise a descriptive ``ValueError``
        rather than an ``AttributeError``/``TypeError``.
        """
        if self.mode not in VALID_MODES:
            raise ValueError(
                f"invalid mode {self.mode!r}; must be one of {VALID_MODES}"
            )
        if self.engine not in VALID_ENGINES:
            raise ValueError(
                f"invalid engine {self.engine!r}; "
                f"must be one of {VALID_ENGINES}"
            )
        if self.segmentation not in VALID_SEGMENTATIONS:
            raise ValueError(
                f"invalid segmentation {self.segmentation!r}; "
                f"must be one of {VALID_SEGMENTATIONS}"
            )
        if self.segmentation == "hold" and (
            self.engine not in ("parakeet", "parakeet-mlx")
            or self.mode != "toggle"
        ):
            raise ValueError(
                'segmentation "hold" requires a parakeet engine and '
                'mode "toggle" (wake/vad modes need per-utterance VAD)'
            )
        if self.parakeet_model not in VALID_PARAKEET_MODELS:
            raise ValueError(
                f"invalid parakeet_model {self.parakeet_model!r}; "
                f"must be one of {VALID_PARAKEET_MODELS}"
            )
        if (
            not isinstance(self.parakeet_threads, int)
            or isinstance(self.parakeet_threads, bool)
            or not 1 <= self.parakeet_threads <= 32
        ):
            raise ValueError(
                "parakeet_threads must be an integer between 1 and 32"
            )
        if self.model_arch not in VALID_MODEL_ARCHS:
            raise ValueError(
                f"invalid model_arch {self.model_arch!r}; "
                f"must be one of {VALID_MODEL_ARCHS}"
            )
        if not isinstance(self.mlx_model, str) or not self.mlx_model.strip():
            raise ValueError(
                f"mlx_model must be a non-empty string (a HuggingFace "
                f"model id), got {self.mlx_model!r}"
            )
        for name in ("strip_fillers", "trailing_space", "sounds", "overlay"):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise ValueError(
                    f"{name} must be a boolean (true/false), "
                    f"got {value!r}"
                )
        for name in ("language", "hotkey", "wake_word", "stop_phrase"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(
                    f"{name} must be a string, got {value!r}"
                )
        if self.mode == "wake" and not self.wake_word.strip():
            raise ValueError('mode "wake" requires a non-empty wake_word')
        if isinstance(self.idle_timeout, bool) or not isinstance(
            self.idle_timeout, (int, float)
        ):
            raise ValueError(
                f"idle_timeout must be a number of seconds, "
                f"got {self.idle_timeout!r}"
            )
        if isinstance(self.idle_timeout, float) and not math.isfinite(
            self.idle_timeout
        ):
            # TOML accepts nan/inf literals; NaN would make the wake-mode
            # expiry comparison permanently false and infinity would
            # silently disable re-arming. Only floats can be non-finite:
            # Python ints are always finite, and math.isfinite() would
            # raise OverflowError on ints too large for a float.
            raise ValueError(
                f"idle_timeout must be finite, got {self.idle_timeout!r}"
            )
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        if not isinstance(self.submit_phrases, list) or any(
            not isinstance(p, str) or not p.strip()
            for p in self.submit_phrases
        ):
            raise ValueError(
                "submit_phrases must be a list of non-empty strings"
            )
        if not isinstance(self.wake_word_aliases, list) or any(
            not isinstance(p, str) or not p.strip()
            for p in self.wake_word_aliases
        ):
            raise ValueError(
                "wake_word_aliases must be a list of non-empty strings"
            )
        for field_name in (
            "sound_start",
            "sound_stop",
            "paste_hotkey",
            "cancel_hotkey",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"{field_name} must be a string")
        if not isinstance(self.copy_to_clipboard, bool):
            raise ValueError("copy_to_clipboard must be a boolean")


def load_config(
    path: Path | None = None, overrides: dict[str, Any] | None = None
) -> Config:
    """Load config from TOML, apply overrides, and validate.

    Args:
        path: Config file path; defaults to ``DEFAULT_CONFIG_PATH``. A
            missing file is fine (defaults are used). Passing an explicit
            path that doesn't exist is an error.
        overrides: Field values (e.g. from CLI flags) that take precedence
            over the file. ``None`` values are ignored.

    Returns:
        A validated ``Config``.
    """
    explicit = path is not None
    path = path or DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    elif explicit:
        raise FileNotFoundError(f"config file not found: {path}")

    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(
            f"unknown config keys in {path}: {sorted(unknown)}"
        )
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    cfg = Config(**data)
    cfg.validate()
    return cfg


def sample_config() -> str:
    """Return a commented sample config file as a string."""
    return '''\
# voice-type configuration (~/.config/voice-type/config.toml)
# Every key is optional; the values below are the defaults.

# Activation mode:
#   "toggle" - start paused; the hotkey starts/stops dictation
#   "vad"    - start dictating immediately (hands-free)
#   "wake"   - start passive; saying the wake word starts dictation
mode = "toggle"

# Transcription backend:
#   "moonshine"    - Moonshine streaming models (voice extra)
#   "parakeet"     - Parakeet-TDT 0.6b v3 on CPU via sherpa-onnx
#                    (voice-parakeet extra; ~490 MB download)
#   "parakeet-mlx" - Parakeet-TDT 0.6b v3 on the Apple GPU via MLX:
#                    fp16 accuracy at ~40x realtime — best accuracy
#                    AND speed (voice-mlx extra; Apple Silicon only)
engine = "moonshine"

# HuggingFace model id for the parakeet-mlx engine.
mlx_model = "mlx-community/parakeet-tdt-0.6b-v3"

# How speech becomes text (parakeet engine, toggle mode only):
#   "vad"  - each utterance types when you pause (default)
#   "hold" - record from toggle-on to toggle-off, transcribe the whole
#            take at once: full context, no mid-sentence chopping
segmentation = "vad"

# Parakeet build: "v3-int8" (multilingual, ~490 MB download) or
# "v2-fp16" (English-only, ~1.1 GB, higher precision = better accuracy)
parakeet_model = "v3-int8"

# CPU threads for Parakeet decoding. 4 is fastest for v3-int8 on
# Apple Silicon (~32x realtime); v2-fp16 benefits from 8 (~16x).
parakeet_threads = 4

# Floating waveform pill (macOS): shown ONLY while recording — red
# waves as you speak; hidden when paused/waiting. Click-through; never
# steals focus.
overlay = true

# Remove standalone filler words (uh, um, ...) from typed text.
strip_fillers = true

# Moonshine model: tiny | base | tiny-streaming | base-streaming |
# small-streaming | medium-streaming (most accurate; ~245M params)
model_arch = "medium-streaming"

# Language tag (e.g. "en", "es", "ja"). Non-English models are under
# the non-commercial Moonshine Community License.
language = "en"

# Global toggle hotkey, pynput syntax. Examples: "<ctrl>+;",
# "<ctrl>+<alt>+d", "<cmd>+<shift>+v"
hotkey = "<ctrl>+;"

# Wake word / stop phrase. The wake word is used in "wake" mode; the
# stop phrase deactivates dictation wherever utterances are
# transcribed as you pause (segmentation "vad"). A "hold" take is raw
# audio until toggle-off — no phrase can be heard mid-take — so stop
# with the hotkey (or Esc to cancel) instead. Matching is case- and
# punctuation-insensitive.
wake_word = "claude"

# Alternate spellings the transcriber may produce for your wake word --
# check the terminal's 'heard (awaiting wake word)' lines and add
# whatever it actually printed, e.g.:
# wake_word_aliases = ["claud", "clod", "clawed"]
wake_word_aliases = []

stop_phrase = "stop listening"

# Saying one of these as an ENTIRE utterance (pause, say it, pause)
# presses Enter in the focused app -- e.g. to submit a prompt.
# Set to [] to disable.
submit_phrases = ["over", "go", "submit"]

# In "wake" mode, go back to passive after this many seconds of silence.
idle_timeout = 20.0

# Append a space after each typed utterance.
trailing_space = true

# Play a sound when dictation starts/stops (macOS only). Names are
# system sounds from /System/Library/Sounds (Glass, Hero, Ping, Tink,
# Bottle, ...) or absolute paths to audio files.
sounds = true
sound_start = "Glass"
sound_stop = "Bottle"

# Also keep each dictation session's text on the clipboard.
copy_to_clipboard = false

# Optional global chord that RE-TYPES the last session's transcript at
# the cursor — rescues dictation typed into the wrong window. Empty
# string disables.
paste_hotkey = ""

# Cancel a recording in progress, discarding everything. Only
# intercepted WHILE recording; otherwise the key reaches the focused
# app normally. Empty string disables.
cancel_hotkey = "<esc>"
'''


def write_sample_config(path: Path | None = None, force: bool = False) -> Path:
    """Write the sample config to ``path``, creating parent dirs.

    Args:
        path: Destination; defaults to ``DEFAULT_CONFIG_PATH``.
        force: Overwrite an existing file.

    Returns:
        The path written.
    """
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if force:
        path.write_text(sample_config())
        return path
    try:
        # Exclusive create: an exists-then-write sequence would silently
        # overwrite a config created concurrently between the two steps.
        with open(path, "x") as f:
            f.write(sample_config())
    except FileExistsError:
        raise FileExistsError(
            f"{path} exists (use --force to overwrite)"
        ) from None
    return path
