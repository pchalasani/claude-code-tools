"""Pure logic for optional LLM transcript cleanup ("S1-mini" by "Superwhisper").

The prompt contract, output sanitizing, validation (when to fall back to
the raw transcript) and chunking all live here, free of I/O, so they are
trivially unit-testable. Process management and HTTP live in
``postprocess_server``. See ``docs/design-post-processing.md``.
"""

from __future__ import annotations

import re

# Verbatim from the model card: the model was trained on exactly this
# system prompt, and a reworded one can produce garbled output.
SYSTEM_PROMPT = (
    "You are a text normalizer for speech-to-text transcripts. The input "
    "begins with a control line specifying the styling, structure, and "
    "context settings; clean the transcript to match those settings and "
    "output only the cleaned text."
)

# The only control-line values the model was trained on.
VALID_STYLES = ("casual", "semi-casual", "semi-formal", "formal")
VALID_STRUCTURES = ("prose", "lists")
VALID_CONTEXTS = ("general", "email")

# Chunk size in words. The card recommends <= ~1,000 input tokens; 250
# words (~330 tokens) leaves room for the output in a 2048-token
# context and keeps each request well under a second.
CHUNK_WORDS = 250

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SPECIAL_TOKEN_RE = re.compile(r"<\|im_(?:start|end)\|>|\[end of text\]")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_LABEL_RE = re.compile(
    r"^(?:cleaned text|cleaned transcript|transcription|output)\s*:\s*",
    re.IGNORECASE,
)
# Phrases a chat-tuned model emits when it answers or plans instead of
# normalizing; their presence means the output is not the transcript.
_ASSISTANT_MARKERS = (
    "analysis:",
    "action plan:",
    "the user is asking",
    "here is the cleaned",
    "here's the cleaned",
    "as an ai",
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def control_line(style: str, structure: str, context: str) -> str:
    """Return the model's control line, e.g. ``[Styling: formal] ...``."""
    return (
        f"[Styling: {style}] [Structure: {structure}] [Context: {context}]"
    )


def build_messages(
    text: str, style: str, structure: str, context: str
) -> list[dict[str, str]]:
    """Return the OpenAI-style chat messages for one cleanup request."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"{control_line(style, structure, context)}\n{text}",
        },
    ]


def max_output_tokens(text: str) -> int:
    """Output token cap: the card's ~1.3x input tokens + 32.

    Tokens are estimated as 4/3 per word (English BPE averages a little
    over one token per word).
    """
    approx_tokens = int(len(text.split()) * 4 / 3) + 1
    return int(1.3 * approx_tokens) + 32


def sanitize_output(text: str) -> str:
    """Strip template debris the model or server may leave around the text."""
    text = _THINK_RE.sub("", text)
    text = _SPECIAL_TOKEN_RE.sub("", text)
    text = text.strip()
    text = _FENCE_RE.sub("", text).strip()
    text = _LABEL_RE.sub("", text).strip()
    return text


def rejection_reason(raw: str, cleaned: str) -> str | None:
    """Return why ``cleaned`` must not replace ``raw``, or None if it may.

    The rules follow Muesli's (expansion limits, assistant chatter) plus
    two of our own: an empty result is rejected (voxtype's regex has
    already removed pure-filler takes, so emptiness here means the model
    dropped real words), and so is a result that lost most of the input.
    """
    if not cleaned.strip():
        return "empty output"
    if len(cleaned) <= 8 and not re.search(r"[^\W_]", cleaned):
        return "punctuation-only output"
    lowered = cleaned.casefold()
    for marker in _ASSISTANT_MARKERS:
        if marker in lowered and marker not in raw.casefold():
            return f"assistant chatter ({marker!r})"
    n_raw, n_out = len(raw), len(cleaned)
    if n_raw < 50:
        limit, floor = 4.0, 80
    elif n_raw < 150:
        limit, floor = 2.5, 150
    else:
        limit, floor = 2.0, 200
    if n_out > floor and n_out > limit * n_raw:
        return "output much longer than input"
    if n_raw > 40 and n_out < 0.35 * n_raw:
        return "output lost most of the input"
    return None


def mostly_non_ascii(text: str) -> bool:
    """True if most letters are non-ASCII (S1-mini is English-only)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for c in letters if ord(c) > 127)
    return non_ascii > len(letters) / 2


def split_chunks(text: str, max_words: int = CHUNK_WORDS) -> list[str]:
    """Split ``text`` at sentence ends into chunks of <= ``max_words``.

    Parakeet punctuates its output, so sentence boundaries are reliable.
    A single sentence longer than ``max_words`` is split at word
    boundaries. Joining the chunks with spaces reproduces the text up to
    whitespace.
    """
    words_total = len(text.split())
    if words_total <= max_words:
        return [text] if text.strip() else []
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in _SENTENCE_END_RE.split(text.strip()):
        words = sentence.split()
        while len(words) > max_words:
            if current:
                chunks.append(" ".join(current))
                current, count = [], 0
            chunks.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if not words:
            continue
        if count + len(words) > max_words and current:
            chunks.append(" ".join(current))
            current, count = [], 0
        current.append(" ".join(words))
        count += len(words)
    if current:
        chunks.append(" ".join(current))
    return chunks
