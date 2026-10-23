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

# Provider-independent settings (see config.Config).
VALID_PROVIDERS = ("s1-mini", "cerebras")
# Cerebras formatting modes, conveyed through its prompt.
VALID_FORMATS = ("auto", "paragraphs", "minimal")
# How line breaks in cleaned text are typed: Shift+Enter, or flattened
# to spaces (one line). "auto" = Shift+Enter for cerebras (whose point
# is structure), flatten for s1-mini (its long-standing behavior). A
# bare Enter is never typed mid-take.
VALID_NEWLINES = ("auto", "shift_enter", "flatten")
# Per-provider default timeouts (post_process_timeout = 0 means these).
DEFAULT_TIMEOUTS = {"s1-mini": 4.0, "cerebras": 2.0}



def effective_newlines(newlines: str, provider: str) -> str:
    """Resolve ``post_process_newlines`` ("auto" per provider)."""
    if newlines != "auto":
        return newlines
    return "shift_enter" if provider == "cerebras" else "flatten"


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


_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]+")


def strip_controls(text: str) -> str:
    """Normalize line breaks to "\\n"; replace every other control
    character (tab, Esc, backspace, ...) with a space; trim.

    LLM output is untrusted: typed, a lone "\\r" is Enter and a tab moves
    focus; printed, an Esc sequence drives the terminal.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_RE.sub(" ", text).strip()


def flatten_lines(text: str) -> str:
    """Collapse all whitespace, newlines included, to single spaces.

    The typist sends a newline as the Enter key, which would submit a
    chat prompt or run a shell command partway through a take, so
    multi-line model output ("lists" structure, "email" context, and
    occasionally casual styling) is typed as one line.
    """
    return " ".join(text.split())


# Words a cleanup may write out in full for a spoken contraction: the
# pieces "can't" splits into ("can", "t") map to what they stand for.
_CONTRACTED = {
    "t": ("not", "cannot"),
    "ll": ("will",),
    "m": ("am",),
    "re": ("are",),
    "s": ("is", "has"),
    "ve": ("have",),
    "d": ("would", "had"),
    "won": ("will",),
    "cannot": ("can", "not"),
    "gonna": ("going", "to"),
    "wanna": ("want", "to"),
}


def _words(text: str) -> set[str]:
    """The distinct words and numbers of ``text``, case-folded."""
    return {w.casefold() for w in re.findall(r"[^\W_]+", text)}


def rejection_reason(raw: str, cleaned: str) -> str | None:
    """Return why ``cleaned`` must not replace ``raw``, or None if it may.

    The rules follow Muesli's (expansion limits, assistant chatter) plus
    two of our own: an empty result is rejected (voxtype's regex has
    already removed pure-filler takes, so emptiness here means the model
    dropped real words), and so is a much shorter result that uses words
    the user never said (a reply or summary rather than a cleanup).
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
    raw_words = _words(raw)
    out_words = _words(cleaned)
    shrank = (n_raw > 40 and n_out < 0.35 * n_raw) or (
        len(raw.split()) >= 4
        and len(cleaned.split()) < 0.35 * len(raw.split())
    )
    # A big cut is fine when the model only deleted ("..., scratch that,
    # hello" -> "Hello."): its output is then made of the user's own
    # words, contractions read either way ("can't" <-> "cannot"). A
    # reply or summary ("OK.", "I can.") brings in a word the user never
    # said, and output with no words at all replaces the take with
    # nothing. A short take formatted as a number ("one hundred twenty
    # three kilograms" -> "123 kg") changes its words, so it is let
    # through.
    raw_words |= {
        full
        for word in raw_words
        for full in _CONTRACTED.get(word, ())
    }
    new_words = {
        w
        for w in out_words - raw_words
        if not set(_CONTRACTED.get(w, ())) & raw_words
    }
    number_format = n_raw <= 40 and re.search(r"\d", cleaned)
    if shrank and (new_words or not out_words) and not number_format:
        return "output lost most of the input"
    return None


def mostly_non_ascii(text: str) -> bool:
    """True if most letters are non-ASCII (S1-mini is English-only)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for c in letters if ord(c) > 127)
    return non_ascii > len(letters) / 2


# Common function words, for a cheap "is this English?" check: S1-mini
# is English-only, and Parakeet transcribes many languages. A take is
# judged non-English only on POSITIVE evidence — another language's
# function words — because terse English dictation ("first install
# voxtype second run setup") can have almost no English ones.
_ENGLISH_COMMON = frozenset("""
a about after all also am an and any are as at be because been but by
can could did do does don't for from had has have he her him his how i
i'm if in into is it it's its just let's like me more my no not now of
on one or our out so some than that that's the their them then there
these they this to up us was we were what when where which who why will
with would you your
""".split())
_FOREIGN_COMMON = {
    "es": "de la que el en y los se del las un por con una su para es al "
    "lo como cómo más pero sus le ya este sí porque esta entre cuando muy "
    "sin sobre también hasta hay donde desde todo nos ni ese eso esto "
    "qué yo él ella estar estás quiero tú tu",
    "fr": "le la les de des du un une et est en que qui dans pour pas sur "
    "au avec ce il je ne se son sa ses nous vous mais ou où plus par "
    "c'est j'ai",
    "de": "der die und den von zu das mit sich des auf für ist im dem "
    "nicht ein eine als auch es werden aus er hat dass sie nach wird bei "
    "einer um sind noch wie einem über einen zum haben nur oder aber vor "
    "zur bis mehr durch man ich wir sollten",
    "it": "il di che è e la per un non una sono mi ho lo ma ha le si con "
    "cosa come io questo della nel",
    "pt": "o os as de do da em um uma que não para com por mais mas eu "
    "ele ela você isso está",
    "nl": "de het een en van ik te dat die niet op aan met voor zijn er "
    "maar om ook als dan wat",
    "id": "yang dan di ini itu dengan untuk tidak dari dalam akan pada "
    "juga saya ke karena ada bisa kita harus",
}
# Each language's set minus words English also uses ("a", "in", ...).
_FOREIGN_SETS = [
    frozenset(words.split()) - _ENGLISH_COMMON
    for words in _FOREIGN_COMMON.values()
]
# A take this share (or more) of one other language's function words,
# and more of them than English ones, is not English.
_FOREIGN_MIN_SHARE = 0.25
# Shorter takes are too small to judge; they are treated as English.
_LANG_MIN_WORDS = 4
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def likely_english(text: str) -> bool:
    """Cheap check that ``text`` is English (S1-mini is English-only).

    False for mostly non-ASCII text, and for text whose words are
    largely another language's function words ("quiero mover la reunión
    al jueves"). Terse English without function words stays English.
    """
    if mostly_non_ascii(text):
        return False
    words = [
        w.replace("’", "'") for w in _WORD_RE.findall(text.casefold())
    ]
    if len(words) < _LANG_MIN_WORDS:
        return True
    n = len(words)
    english = sum(w in _ENGLISH_COMMON for w in words) / n
    foreign = max(sum(w in fs for w in words) / n for fs in _FOREIGN_SETS)
    return not (foreign >= _FOREIGN_MIN_SHARE and foreign > english)


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
