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

VALID_ENGINES = ("moonshine", "parakeet")

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
        strip_fillers: Drop standalone filler words (uh, um, ...) from
            typed text.
        model_arch: Moonshine model architecture name (moonshine engine
            only).
        language: Language tag understood by Moonshine (e.g. "en").
        hotkey: Global toggle hotkey in pynput syntax, e.g. "<ctrl>+;".
        wake_word: Phrase that activates dictation in "wake" mode.
        stop_phrase: Spoken phrase that deactivates dictation.
        submit_phrases: Phrases that press Enter when spoken as an
            entire utterance (e.g. say "go" alone to submit).
        idle_timeout: Seconds of silence after which "wake" mode re-arms.
        trailing_space: Append a space after each typed utterance.
        sounds: Play a system sound on activate/deactivate (macOS).
    """

    mode: str = "toggle"
    engine: str = "moonshine"
    strip_fillers: bool = True
    model_arch: str = "medium-streaming"
    language: str = "en"
    hotkey: str = "<ctrl>+;"
    wake_word: str = "claude"
    stop_phrase: str = "stop listening"
    submit_phrases: list[str] = field(
        default_factory=lambda: ["over", "go", "submit"]
    )
    idle_timeout: float = 20.0
    trailing_space: bool = True
    sounds: bool = True

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
        if self.model_arch not in VALID_MODEL_ARCHS:
            raise ValueError(
                f"invalid model_arch {self.model_arch!r}; "
                f"must be one of {VALID_MODEL_ARCHS}"
            )
        for name in ("strip_fillers", "trailing_space", "sounds"):
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
#   "moonshine" - Moonshine streaming models (voice extra)
#   "parakeet"  - NVIDIA Parakeet-TDT 0.6b v3 via sherpa-onnx
#                 (voice-parakeet extra; ~490 MB one-time download;
#                 cleaner transcripts, drops filler words natively)
engine = "moonshine"

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

# Wake word / stop phrase (used in "wake" mode; stop phrase works in
# every mode). Matching is case- and punctuation-insensitive.
wake_word = "claude"
stop_phrase = "stop listening"

# Saying one of these as an ENTIRE utterance (pause, say it, pause)
# presses Enter in the focused app -- e.g. to submit a prompt.
# Set to [] to disable.
submit_phrases = ["over", "go", "submit"]

# In "wake" mode, go back to passive after this many seconds of silence.
idle_timeout = 20.0

# Append a space after each typed utterance.
trailing_space = true

# Play a system sound when dictation starts/stops (macOS only).
sounds = true
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
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sample_config())
    return path
