"""Screen-scraping heuristics: what harness is this, and what is it doing.

Everything here is deliberately pure (string in, verdict out) so it can be
unit-tested against captured fixtures without a live tmux server.

Why screen-scraping at all: ``pane_current_command`` is useless for
identifying these harnesses -- Claude Code reports a bare version string like
``2.1.220`` because it runs a versioned binary, and Codex reports ``node``.
Process *argv* identifies the harness reliably; the *screen* is the only place
that reveals what it is currently doing.
"""

from __future__ import annotations

import re

from .model import Kind, State

# --- harness identification (from the pane's child process argv) -----------

#: Interpreters that launch an agent via a script path rather than being the
#: agent themselves (Codex ships as JS run under node).
_INTERPRETERS = {"node", "node.exe", "python", "python3", "bun", "deno"}

#: Script-path fragments that identify a harness when run under an interpreter.
_CODEX_PATH = re.compile(r"@openai/codex|/codex(\.js|-cli)?\b")
_CLAUDE_PATH = re.compile(r"/claude(\.js|-code)?\b|/\.local/share/claude/")


def _basename(token: str) -> str:
    """Executable name from an argv[0] token."""
    return token.rsplit("/", 1)[-1]


def classify_argv(argv: str) -> Kind | None:
    """Identify the harness from a single process command line.

    Only the EXECUTABLE decides -- argv[0], or the script path when argv[0] is
    an interpreter. Searching the whole command line matched arguments too, so
    an ordinary ``rg claude .`` in a pane was listed as an idle Claude agent.

    Args:
        argv: One process's command line.

    Returns:
        ``"codex"``, ``"claude"``, or ``None`` when this is not an agent.
    """
    for line in argv.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        exe = _basename(tokens[0])
        if exe in ("codex", "codex-cli"):
            return "codex"
        if exe in ("claude", "claude-code"):
            return "claude"
        # Versioned Claude binaries are named after the version (2.1.220).
        if re.fullmatch(r"\d+\.\d+\.\d+", exe):
            return "claude"
        if exe in _INTERPRETERS and len(tokens) > 1:
            script = tokens[1]
            if _CODEX_PATH.search(script):
                return "codex"
            if _CLAUDE_PATH.search(script):
                return "claude"
    return None


# --- state detection (from the pane's visible screen) ----------------------

#: Claude renders AskUserQuestion and permission prompts as numbered choices.
_ASKING = re.compile(
    r"❯\s*1\.|Do you want|Would you like|^\s*1\.\s*Yes|\(y/n\)|"
    r"Select an option|Choose an option",
    re.MULTILINE,
)
#: The MAIN agent is mid-turn. Two forms seen live:
#:   "✻ Cooked for 14m 17s · esc to interrupt"
#:   "· Razzmatazzing… (1m 42s · ↓ 4.8k tokens · thought for 14s)"
#: The second has no "esc to interrupt" at all, so matching only that phrase
#: reported an actively working pane as idle. The parenthesised
#: elapsed-time-plus-token-counter is what distinguishes it from a subagent
#: row, which carries the same counter WITHOUT parentheses.
_BUSY = re.compile(
    r"esc to interrupt|\(\d+[ms][^)]*↓[^)]*tokens",
    re.IGNORECASE,
)
#: Background work. Two forms, both taken from live panes:
#:   "✻ Baked for 16m 24s · 1 monitor still running"
#:   "  ◯ cache-incremental-evidence  Grepping ...   1m 16s · ↓ 873.5k tokens"
#: The second is Claude's subagent tree: ◯ marks a subagent still RUNNING
#: (✓ marks a finished one, and ⏺ is ordinary assistant output, so neither of
#: those can be used). Without this a pane with a working subagent but a free
#: main prompt reported as idle.
_BG = re.compile(
    r"\b\d+\s+monitors?\b|^\s*◯\s+\S",
    re.MULTILINE,
)

#: Codex chrome to ignore when looking for its last content line.
_CODEX_CHROME = re.compile(
    r"^\s*[›❯]|·|gpt-[\d.]|^\s*─|esc to|tab to|^\s*$|^\s*[▌│]"
)


#: A pending prompt is at the BOTTOM of the screen. Searching all retained
#: text made any earlier sentence containing e.g. "Would you like" pin the
#: pane to `input` until it scrolled off.
_PROMPT_TAIL_LINES = 12


def detect_state(screen: str, kind: Kind) -> State:
    """Classify what the agent is doing from its visible screen.

    Args:
        screen: The pane's captured text (escape sequences stripped).
        kind: Which harness is running, from :func:`classify_argv`.

    Returns:
        One of ``input`` (waiting on the user), ``busy`` (mid-turn),
        ``bg`` (background monitors running), or ``idle``.
    """
    tail_lines = screen.splitlines()[-_PROMPT_TAIL_LINES:]
    tail = "\n".join(tail_lines)
    # An ANSWERED prompt keeps its choices on screen, with Claude's footer
    # rendered below them. Only treat a prompt as pending when nothing from
    # the footer appears after it -- otherwise a just-answered
    # AskUserQuestion stays flagged as blocking until it scrolls off.
    if _ASKING.search(tail) and not _footer_below_prompt(tail_lines):
        return "input"
    # BUSY must be decided before the Codex question heuristic. A visible
    # spinner is proof the agent is working, whereas the heuristic can only
    # guess from an older question still on screen: Codex asks, you answer,
    # it starts working -- and the question is still up there.
    if _BUSY.search(screen):
        return "busy"
    if kind == "codex" and _codex_awaiting_answer(tail):
        return "input"
    if _BG.search(screen):
        return "bg"
    return "idle"


#: Footer chrome Claude renders BELOW a completed exchange. Anchored to the
#: start of a line: an option in a PENDING prompt can quote this text
#: ("2. Explain why the status says bypass permissions on") and must not be
#: mistaken for the footer itself.
_FOOTER = re.compile(
    r"^\s*("
    r"⏵⏵|ctx [█░]|new task\?|✻|※"       # Claude chrome
    r"|gpt-[\w.\-]+\s|›"                # Codex status line and input box
    r")"
)


def _footer_below_prompt(lines: list[str]) -> bool:
    """Whether harness footer chrome appears after the last prompt line."""
    last_prompt = -1
    for index, line in enumerate(lines):
        if _ASKING.search(line):
            last_prompt = index
    if last_prompt < 0:
        return False
    return any(_FOOTER.search(line) for line in lines[last_prompt + 1 :])

def _codex_awaiting_answer(screen: str) -> bool:
    """Heuristic: Codex asked a question and stopped.

    Codex has no structured prompt UI, so the only available signal is that
    the last real content line -- ignoring the input box and status chrome --
    ends in a question mark. This has false positives (an answer that merely
    ends in a question) and misses (a question phrased as a statement).

    Only the screen TAIL is considered, and callers must rule out "busy"
    first: a question further up the scrollback says nothing about whether
    the agent is still waiting for you.
    """
    for line in reversed(screen.splitlines()):
        if not line.strip() or _CODEX_CHROME.search(line):
            continue
        return line.rstrip().endswith("?")
    return False


# --- context extraction ----------------------------------------------------

_CODEX_STATUS = re.compile(r"(gpt-[\w.\-]+)\s+(\w+)?\s*·\s*([^·]+)·\s*([^·\n]+)")
_CLAUDE_MODEL = re.compile(
    r"\b(opus[\w.\-\[\]]*|sonnet[\w.\-\[\]]*|haiku[\w.\-\[\]]*|fable[\w.\-\[\]]*)"
)
_SEPARATOR_NAME = re.compile(r"─\s([A-Za-z0-9][A-Za-z0-9._-]{3,})\s─")
_ARGV_NAME = re.compile(r"--(?:resume|name)[= ]([^\s]+)")
_SPINNER = re.compile(r"^[✳*⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\s]+")


def extract_model(screen: str, kind: Kind) -> str:
    """Pull the model identifier out of the harness footer, if present."""
    if kind == "codex":
        match = _CODEX_STATUS.search(screen)
        return match.group(1) if match else ""
    match = _CLAUDE_MODEL.search(screen)
    return match.group(1) if match else ""


def extract_name(argv: str, pane_title: str, screen: str) -> str:
    """Best-effort agent session name, most reliable source first.

    Order: the harness's own argv (``--resume``/``--name``), then the tmux
    pane title (Claude sets it to ``✳ <name>``), then an on-screen separator
    line. Codex rarely carries a name in any of these and returns ``""``.
    """
    match = _ARGV_NAME.search(argv)
    if match:
        return match.group(1)

    title = _SPINNER.sub("", pane_title).strip()
    # Shell-set titles are paths, ~[dir] forms, or hostnames -- not names.
    if title and not re.search(r"[/\[\]]|\.lan$|\.local$", title):
        return title

    names = _SEPARATOR_NAME.findall(screen)
    return names[-1] if names else ""


def extract_info(screen: str, kind: Kind) -> str:
    """One line of context for the list view (model, repo, branch, …)."""
    if kind == "codex":
        match = _CODEX_STATUS.search(screen)
        if match:
            parts = [p.strip() for p in match.groups() if p]
            return " · ".join(parts)
    lines = [ln.strip() for ln in screen.splitlines() if ln.strip()]
    for line in reversed(lines):
        if _CLAUDE_MODEL.search(line):
            return re.sub(r"\s{2,}", " ", line)[:80]
    return lines[-1][:80] if lines else ""
