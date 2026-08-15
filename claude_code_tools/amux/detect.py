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

_CODEX_ARGV = re.compile(r"(^|/|\s)codex(\s|$)|@openai/codex")
_CLAUDE_ARGV = re.compile(r"(^|/|\s)claude(\s|$)")


def classify_argv(argv: str) -> Kind | None:
    """Identify the harness from a pane child process's command line.

    Args:
        argv: Newline-joined command lines of the pane's child processes.

    Returns:
        ``"codex"``, ``"claude"``, or ``None`` when no agent is running.
    """
    if _CODEX_ARGV.search(argv):
        return "codex"
    if _CLAUDE_ARGV.search(argv):
        return "claude"
    return None


# --- state detection (from the pane's visible screen) ----------------------

#: Claude renders AskUserQuestion and permission prompts as numbered choices.
_ASKING = re.compile(
    r"❯\s*1\.|Do you want|Would you like|^\s*1\.\s*Yes|\(y/n\)|"
    r"Select an option|Choose an option",
    re.MULTILINE,
)
_BUSY = re.compile(r"esc to interrupt", re.IGNORECASE)
_BG = re.compile(r"\b\d+\s+monitors?\b")

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
    if kind == "codex" and _codex_awaiting_answer(screen):
        return "input"
    if _BUSY.search(screen):
        return "busy"
    if _BG.search(screen):
        return "bg"
    return "idle"


#: Footer chrome Claude renders BELOW a completed exchange.
_FOOTER = re.compile(r"bypass permissions on|ctx [█░]|new task\?|/clear to save")


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
