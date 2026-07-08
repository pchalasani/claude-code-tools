#!/usr/bin/env python3
"""
Hook to handle session-related triggers in Claude Code.

Triggers:
- '>resume', '>continue', '>handoff': Copy session ID + show resume instructions
- '>session', '>session-id': Copy session ID + show simple confirmation
- '>trim [options]': Preview in-place trim of the CURRENT session (tokens
  saved), then '>trim yes' applies it / '>trim cancel' abandons it.

Standalone (stdlib only): Claude Code may run this under a Python without
the claude_code_tools package installed. Heavy lifting for '>trim' is
delegated to the `aichat trim-in-place` CLI (a plugin prerequisite).
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Optional

# Trigger patterns for resume workflow (copy + show resume instructions)
RESUME_TRIGGERS = (">resume", ">continue", ">handoff")

# Trigger patterns for just copying session ID (simple confirmation)
SESSION_ID_TRIGGERS = (">session", ">session-id")

# Trigger for in-place trim of the current session
TRIM_TRIGGER = ">trim"

# Where the pending trim plan (between preview and '>trim yes') is stored
TRIM_STATE_DIR = os.environ.get("AICHAT_TRIM_STATE_DIR", "/tmp/claude")

# A preview is applicable for this long; after that '>trim yes' asks for
# a fresh preview
TRIM_PENDING_TTL_SECS = 600

# A pending-state timestamp further in the future than this (beyond
# plausible clock skew) marks the state file as corrupt/hostile.
TRIM_PENDING_FUTURE_SLACK_SECS = 60

# The aichat CLI binary (env override is for tests)
AICHAT_BIN = os.environ.get("AICHAT_BIN", "aichat")

TRIM_DEFAULT_THRESHOLD = 500

# ANSI colors
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CODE = "\033[37m"
RESET = "\033[0m"


def copy_to_clipboard(text: str) -> bool:
    """
    Copy text to clipboard. Tries multiple commands for cross-platform support.
    Returns True if successful, False otherwise.
    """
    # Commands to try in order (first one that works wins)
    clipboard_commands = [
        ["pbcopy"],  # macOS
        ["xclip", "-selection", "clipboard"],  # Linux X11
        ["xsel", "--clipboard", "--input"],  # Linux X11 alternative
        ["wl-copy"],  # Linux Wayland
        ["clip"],  # Windows
    ]

    for cmd in clipboard_commands:
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode(),
                capture_output=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return True
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue

    return False


def copy_session_id_and_format_message(
    session_id: str,
    show_resume_instructions: bool = False,
) -> str:
    """
    Copy session ID to clipboard and return a formatted message.

    Args:
        session_id: The session ID to copy.
        show_resume_instructions: If True, show full resume instructions.
            If False, show simple confirmation.

    Returns:
        Formatted message string with ANSI colors.
    """
    copied = copy_to_clipboard(session_id)

    # ANSI escape codes for bright blue color and code style
    BLUE = "\033[94m"
    CODE = "\033[37m"  # Regular white for code-like appearance
    RESET = "\033[0m"

    if show_resume_instructions:
        # Full resume workflow message
        if copied:
            return (
                f"{BLUE}Session ID copied to clipboard!{RESET}\n\n"
                f"{BLUE}To continue your work in a new session:{RESET}\n"
                f"{BLUE}  1. Quit Claude (Ctrl+D twice){RESET}\n"
                f"{BLUE}  2. Run: {CODE}`aichat resume <paste>`{RESET}\n\n"
                f"{BLUE}You can then choose between a few different ways of{RESET}\n"
                f"{BLUE}continuing your work.{RESET}\n\n"
                f"{BLUE}Session ID: {session_id}{RESET}"
            )
        else:
            return (
                f"{BLUE}Could not copy to clipboard. Here's your session ID:{RESET}\n\n"
                f"{BLUE}  {session_id}{RESET}\n\n"
                f"{BLUE}To continue your work in a new session:{RESET}\n"
                f"{BLUE}  1. Copy the session ID above{RESET}\n"
                f"{BLUE}  2. Quit Claude (Ctrl+D twice){RESET}\n"
                f"{BLUE}  3. Run: {CODE}`aichat resume <session-id>`{RESET}\n\n"
                f"{BLUE}You can then choose between a few different ways of{RESET}\n"
                f"{BLUE}continuing your work.{RESET}"
            )
    else:
        # Simple confirmation message
        if copied:
            return (
                f"{BLUE}Session ID copied to clipboard!{RESET}\n\n"
                f"{BLUE}Session ID: {session_id}{RESET}"
            )
        else:
            return (
                f"{BLUE}Could not copy to clipboard. Here's your session ID:{RESET}\n\n"
                f"{BLUE}  {session_id}{RESET}"
            )


# Conservative filename-safe shape for session ids (Claude uses UUIDs).
# Anything else (slashes, traversal, non-strings) must never reach a
# path operation; stdin fields are treated as hostile.
_SAFE_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")


def _is_safe_session_id(session_id) -> bool:
    """True if session_id is a string safe to embed in a filename."""
    return isinstance(session_id, str) and bool(
        _SAFE_SESSION_ID_RE.fullmatch(session_id)
    )


def _trim_state_path(session_id: str) -> str:
    return os.path.join(TRIM_STATE_DIR, f"trim-pending.{session_id}.json")


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _secure_state_dir(create: bool) -> Optional[str]:
    """Validate (and optionally create) the private trim-state dir.

    The default state dir lives under shared ``/tmp``, so an existing
    entry is treated as hostile until proven otherwise: it must be a
    real directory (not a symlink or file) owned by the current user.
    If it is ours but group/other-accessible it is chmod'ed to 0700.

    Args:
        create: If True, create the directory (mode 0700) when absent.

    Returns:
        None if the directory is safe to use, else an error message.
    """
    try:
        if create:
            os.makedirs(TRIM_STATE_DIR, mode=0o700, exist_ok=True)
        st = os.lstat(TRIM_STATE_DIR)
    except OSError as e:
        return f"Cannot use trim state dir {TRIM_STATE_DIR}: {e}"
    if not stat.S_ISDIR(st.st_mode):
        return (
            f"Trim state dir {TRIM_STATE_DIR} is not a real directory "
            f"(symlink or file) - refusing to touch trim state there."
        )
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return (
            f"Trim state dir {TRIM_STATE_DIR} is owned by another "
            f"user - refusing to touch trim state there."
        )
    if stat.S_IMODE(st.st_mode) & 0o077:
        try:
            os.chmod(TRIM_STATE_DIR, 0o700)
        except OSError as e:
            return (
                f"Cannot make trim state dir {TRIM_STATE_DIR} "
                f"private: {e}"
            )
    return None


def _save_trim_state(session_id: str, plan: dict) -> Optional[str]:
    """Persist the pending trim plan.

    The plan is written to a private (0600) temp file in the validated
    state dir and atomically renamed into place, so a hostile symlink
    planted at the final path is replaced - never followed/truncated.

    Returns:
        None on success, else an error message.
    """
    err = _secure_state_dir(create=True)
    if err:
        return err
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=TRIM_STATE_DIR, prefix=".trim-state-tmp-"
        )
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(plan, f)
        os.replace(tmp_path, _trim_state_path(session_id))
    except OSError as e:
        if tmp_path is not None:
            _remove_quietly(tmp_path)
        return f"Cannot write trim state under {TRIM_STATE_DIR}: {e}"
    return None


def _is_real_int(value) -> bool:
    """True for int values, excluding bools (bool subclasses int)."""
    return isinstance(value, int) and not isinstance(value, bool)


# Upper magnitude bound for any number the hook trusts from external
# JSON (sizes, token counts, timestamps): far beyond every real value,
# yet small enough that float()/formatting can never overflow (JSON
# ints are unbounded; float(10**400) raises OverflowError).
MAX_TRUSTED_NUMBER = 10**15


def _is_finite_number(value) -> bool:
    """True for FINITE, sanely-sized int/float values.

    Bools are excluded (bool subclasses int), as are NaN/Infinity
    (``json.load`` accepts those literals) and magnitudes beyond
    ``MAX_TRUSTED_NUMBER`` (huge JSON ints overflow ``float()``).
    """
    if _is_real_int(value):
        return -MAX_TRUSTED_NUMBER <= value <= MAX_TRUSTED_NUMBER
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and abs(value) <= MAX_TRUSTED_NUMBER
    )


# Tool-name shape ``parse_trim_args`` can actually save: one segment
# of the accepted tool token (letter-initial word chars plus '.'/'-';
# commas separate segments and are never stored), lowercased before
# saving, and of sane length. Pending-state validation must accept
# EXACTLY this shape - nothing looser.
_MAX_TOOL_NAME_CHARS = 128
_TOOL_NAME_RE = re.compile(r"[A-Za-z][\w.-]*")


def _is_saved_tool_name(name) -> bool:
    """True if ``name`` is a tools entry ``parse_trim_args`` could
    save: a lowercase, letter-initial, bounded-length token segment
    (so no empty strings, commas, or option-looking values)."""
    return (
        isinstance(name, str)
        and len(name) <= _MAX_TOOL_NAME_CHARS
        and name == name.lower()
        and bool(_TOOL_NAME_RE.fullmatch(name))
    )


def _is_valid_trim_plan(plan) -> bool:
    """True if a loaded pending-trim plan has exactly the shape saved
    by ``_save_trim_state`` (state files are treated as hostile)."""
    if not isinstance(plan, dict):
        return False
    if not _is_finite_number(plan.get("created_at")):
        return False
    transcript_path = plan.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return False
    opts = plan.get("opts")
    if not isinstance(opts, dict):
        return False
    threshold = opts.get("threshold")
    if threshold is not None and not (
        _is_real_int(threshold) and threshold > 0
    ):
        return False
    # The parser never produces 0 (and 0 means "trim nothing"), so a
    # zero here is a shape _save_trim_state could not have written.
    trim_assistant = opts.get("trim_assistant")
    if trim_assistant is not None and not (
        _is_real_int(trim_assistant) and trim_assistant != 0
    ):
        return False
    # The parser also rejects duplicates, so a plan with them is
    # corrupt state, not a saved preview.
    tools = opts.get("tools")
    if not isinstance(tools, list) or not all(
        _is_saved_tool_name(t) for t in tools
    ):
        return False
    if len(set(tools)) != len(tools):
        return False
    preview_tokens = plan.get("preview_tokens")
    if preview_tokens is not None and not _is_finite_number(
        preview_tokens
    ):
        return False
    return True


def _load_trim_state(session_id: str) -> Optional[dict]:
    """Return the pending trim plan, or None if absent/expired/corrupt.

    Corrupt-but-parseable state (wrong schema, non-finite or
    far-future timestamps) is deleted and treated exactly like no
    state at all. An unsafe state dir (symlink, foreign owner) is
    treated as having no state.
    """
    if _secure_state_dir(create=False) is not None:
        return None
    path = _trim_state_path(session_id)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd) as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return None
    if not _is_valid_trim_plan(plan):
        _remove_quietly(path)
        return None
    # Pure comparisons (no float() conversion): a huge int timestamp
    # must be rejected, not raise OverflowError. A timestamp beyond
    # plausible clock skew in the future is corrupt/hostile - it would
    # otherwise never expire.
    now = time.time()
    created_at = plan["created_at"]
    if (
        created_at > now + TRIM_PENDING_FUTURE_SLACK_SECS
        or created_at < now - TRIM_PENDING_TTL_SECS
    ):
        _remove_quietly(path)
        return None
    return plan


def _clear_trim_state(session_id: str) -> None:
    """Remove pending state (no-op when the state dir is unsafe)."""
    if _secure_state_dir(create=False) is not None:
        return
    _remove_quietly(_trim_state_path(session_id))


# No legitimate '>trim' number needs more digits than this. Longer
# tokens are rejected BEFORE int(): Python 3.11+ raises ValueError on
# huge digit strings (int-str conversion limit) and older versions
# would burn CPU converting them.
_MAX_NUMERIC_TOKEN_DIGITS = 12


def _parse_int_token(tok: str) -> Optional[int]:
    """Convert a numeric '>trim' token to int; None if unusable."""
    if len(tok.lstrip("+-")) > _MAX_NUMERIC_TOKEN_DIGITS:
        return None
    try:
        return int(tok)
    except ValueError:
        return None


def parse_trim_args(arg: str):
    """Parse shape-based, order-free '>trim' tokens.

    Token shapes: -N/+N = assistant-message spec, bare digits = char
    threshold, letter-initial words = comma-separated tool names.

    Returns:
        (opts, None) on success, (None, error_message) on bad input.
        opts = {"threshold": int|None, "trim_assistant": int|None,
        "tools": [str, ...]}.
    """
    opts = {"threshold": None, "trim_assistant": None, "tools": []}
    for tok in arg.split():
        if re.fullmatch(r"[+-]\d+", tok):
            if opts["trim_assistant"] is not None:
                return None, f"More than one assistant spec ('{tok}')."
            value = _parse_int_token(tok)
            if value is None:
                return None, "Assistant spec is too large."
            if value == 0:
                return None, "Assistant spec must be non-zero (e.g. -20)."
            opts["trim_assistant"] = value
        elif tok.isdigit():
            if opts["threshold"] is not None:
                return None, f"More than one threshold ('{tok}')."
            value = _parse_int_token(tok)
            if value is None:
                return None, "Threshold is too large."
            if value <= 0:
                return None, "Threshold must be a positive number."
            opts["threshold"] = value
        elif re.fullmatch(r"[A-Za-z][\w.-]*(,[A-Za-z][\w.-]*)*", tok):
            # The pattern forbids empty comma segments ('bash,',
            # ',bash', 'bash,,read' all fall through to the error
            # below), so every split name here is non-empty.
            for name in tok.lower().split(","):
                if len(name) > _MAX_TOOL_NAME_CHARS:
                    return None, "Tool name is too long."
                if name in opts["tools"]:
                    return None, f"Duplicate tool name ('{name}')."
                opts["tools"].append(name)
        else:
            return None, f"Unrecognized token: '{tok}'."
    return opts, None


def describe_trim_opts(opts: dict) -> str:
    """Human-readable one-liner for the parsed trim options."""
    parts = []
    ta = opts.get("trim_assistant")
    if ta is not None:
        if ta < 0:
            parts.append(f"keep last {abs(ta)} long assistant msgs")
        else:
            parts.append(f"trim first {ta} long assistant msgs")
    threshold = opts.get("threshold") or TRIM_DEFAULT_THRESHOLD
    parts.append(f"threshold {threshold} chars")
    tools = opts.get("tools")
    parts.append(f"tools: {','.join(tools)}" if tools else "all tools")
    return ", ".join(parts)


def _fmt_size(num_bytes) -> str:
    """Human-readable byte size; '?' for anything unconvertible.

    ``OverflowError`` is caught too: validation bounds numbers before
    they reach formatting, but this must stay crash-proof even if fed
    a huge raw int directly (defense in depth).
    """
    try:
        n = float(num_bytes)
    except (TypeError, ValueError, OverflowError):
        return "?"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{int(n)} B"


# A successful trim-in-place result must carry these keys with these
# shapes before the hook trusts it: booleans it branches on, counts/
# sizes/savings it formats with ',' or compares (the CLI only ever
# emits non-negative integers there - anything else, including
# negative values, floats, NaN/Infinity or huge ints, is a broken or
# hostile CLI), and the path fields it prints.
_RESULT_BOOL_KEYS = ("applied", "dry_run", "nothing_to_trim")
_RESULT_COUNT_KEYS = (
    "num_tools_trimmed",
    "num_assistant_trimmed",
    "chars_saved",
    "tokens_saved",
    "size_before",
    "size_after",
)


def _is_count(value) -> bool:
    """True for non-bool ints in [0, MAX_TRUSTED_NUMBER] - the only
    shape the CLI emits for counts, sizes and savings."""
    return _is_real_int(value) and 0 <= value <= MAX_TRUSTED_NUMBER


def _is_valid_trim_result(data) -> bool:
    """True if CLI stdout matches the trim-in-place result schema.

    The CLI's output is hostile input: every field the hook formats,
    compares, or branches on is checked before use (so e.g.
    ``{"tokens_saved": "many"}``, negative savings, or ``{}`` can
    never arm a trim plan or reach the comma-formatting and raise).
    """
    if not isinstance(data, dict):
        return False
    for key in _RESULT_BOOL_KEYS:
        if not isinstance(data.get(key), bool):
            return False
    for key in _RESULT_COUNT_KEYS:
        if not _is_count(data.get(key)):
            return False
    if not isinstance(data.get("session_file"), str):
        return False
    backup_file = data.get("backup_file")
    if data.get("applied") is True:
        # A real apply always reports where the backup landed.
        if not isinstance(backup_file, str) or not backup_file:
            return False
    elif backup_file is not None and not isinstance(backup_file, str):
        return False
    return True


def run_trim_cli(transcript_path: str, opts: dict, dry_run: bool):
    """Run `aichat trim-in-place` and parse its JSON result.

    The CLI contract is EXACTLY one JSON line on stdout: the result
    object, or {"error": msg} with a nonzero exit. Anything else
    (extra lines around JSON, wrong schema, nonzero exit with a
    result-looking payload) is reported as a graceful error.

    Returns:
        (result_dict, None) on success, (None, error_message) on failure.
    """
    cmd = [
        AICHAT_BIN,
        "trim-in-place",
        transcript_path,
        "--json",
        "--len",
        str(opts.get("threshold") or TRIM_DEFAULT_THRESHOLD),
    ]
    if opts.get("tools"):
        cmd += ["--tools", ",".join(opts["tools"])]
    if opts.get("trim_assistant") is not None:
        cmd += ["--trim-assistant", str(opts["trim_assistant"])]
    if dry_run:
        cmd.append("--dry-run")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=25
        )
    except FileNotFoundError:
        return None, (
            f"`{AICHAT_BIN}` CLI not found. Install it with:\n"
            f"  uv tool install claude-code-tools"
        )
    except OSError as e:
        # E.g. AICHAT_BIN pointing at a directory, a non-executable
        # file, or an empty string (PermissionError and friends).
        return None, (
            f"Cannot run `{AICHAT_BIN}` ({e}).\n"
            f"Check the aichat CLI installation:\n"
            f"  uv tool install --force claude-code-tools"
        )
    except subprocess.SubprocessError:
        return None, "Trim command failed to run (timeout or error)."

    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    payload = None
    if len(lines) == 1:
        try:
            parsed = json.loads(lines[0])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed

    if payload is not None and "error" in payload:
        return None, str(payload["error"])

    if (
        proc.returncode == 0
        and payload is not None
        and _is_valid_trim_result(payload)
    ):
        return payload, None

    detail = proc.stderr.strip() or proc.stdout.strip()
    detail = detail[:400] if detail else f"exit code {proc.returncode}"
    return None, (
        f"aichat trim-in-place failed: {detail}\n\n"
        f"If your aichat CLI predates this feature, update it with:\n"
        f"  uv tool install --force claude-code-tools"
    )


def _trim_usage_lines() -> str:
    return (
        f"{BLUE}Customize (tokens in any order: -N/+N = assistant "
        f"msgs,{RESET}\n"
        f"{BLUE}number = char threshold, words = tool names):{RESET}\n"
        f"{CODE}  >trim -20            {RESET}"
        f"{BLUE}keep last 20 long assistant msgs{RESET}\n"
        f"{CODE}  >trim -20 800        {RESET}"
        f"{BLUE}... with threshold 800 chars{RESET}\n"
        f"{CODE}  >trim -20 bash,read  {RESET}"
        f"{BLUE}... trim only Bash/Read results{RESET}"
    )


def _trim_help_message() -> str:
    """Pure usage text: no file read, no CLI call, no pending state."""
    return (
        f"{BLUE}>trim - trim the CURRENT session's file in place to save "
        f"tokens.{RESET}\n\n"
        f"{BLUE}Two safe steps - a preview never changes anything; only "
        f"{CODE}>trim yes{RESET}{BLUE} writes:{RESET}\n"
        f"{CODE}  >trim              {RESET}"
        f"{BLUE}preview savings with defaults (threshold 500, all "
        f"tools){RESET}\n"
        f"{CODE}  >trim yes          {RESET}"
        f"{BLUE}apply the previewed trim (expires ~10 min){RESET}\n"
        f"{CODE}  >trim cancel       {RESET}"
        f"{BLUE}abandon the pending preview{RESET}\n\n"
        + _trim_usage_lines()
        + "\n\n"
        f"{BLUE}Same session ID is kept; a timestamped .bak backup is "
        f"saved next to{RESET}\n"
        f"{BLUE}the session file. Savings apply on the NEXT resume of "
        f"this session,{RESET}\n"
        f"{BLUE}not the current context window.{RESET}"
    )


def handle_trim(session_id: str, transcript_path: str, raw_arg: str) -> str:
    """Handle the '>trim' trigger; returns the message to display."""
    arg = raw_arg.strip()
    arg_lower = arg.lower()

    if arg_lower in ("help", "-h", "--help", "?"):
        return _trim_help_message()

    if arg_lower in ("cancel", "no", "abort"):
        _clear_trim_state(session_id)
        return f"{YELLOW}Pending trim abandoned. Nothing was changed.{RESET}"

    if arg_lower in ("yes", "y", "apply", "go"):
        plan = _load_trim_state(session_id)
        if plan is None:
            return (
                f"{YELLOW}No pending trim preview (or it expired).{RESET}\n"
                f"{BLUE}Run {CODE}>trim{RESET}{BLUE} or e.g. "
                f"{CODE}>trim -20{RESET}{BLUE} first.{RESET}"
            )
        # Always apply to the transcript the user actually previewed.
        plan_path = plan["transcript_path"]
        if transcript_path and transcript_path != plan_path:
            _clear_trim_state(session_id)
            return (
                f"{YELLOW}This session's transcript path changed since "
                f"the preview - nothing was trimmed.{RESET}\n"
                f"{BLUE}Run {CODE}>trim{RESET}{BLUE} again for a fresh "
                f"preview.{RESET}"
            )
        result, err = run_trim_cli(
            plan_path, plan.get("opts", {}), dry_run=False
        )
        _clear_trim_state(session_id)
        if err:
            return f"{YELLOW}Trim failed:{RESET}\n{BLUE}{err}{RESET}"
        if result.get("nothing_to_trim"):
            return (
                f"{BLUE}Nothing to trim anymore - the session changed "
                f"since the preview.{RESET}\n"
                f"{BLUE}(~{result.get('tokens_saved', 0):,} tokens of "
                f"savings left; file unchanged){RESET}"
            )
        return (
            f"{GREEN}Session trimmed in place - "
            f"~{result.get('tokens_saved', 0):,} tokens saved!{RESET}\n"
            f"{BLUE}  File size: {_fmt_size(result.get('size_before'))} -> "
            f"{_fmt_size(result.get('size_after'))}{RESET}\n"
            f"{BLUE}  Backup: {result.get('backup_file')}{RESET}\n\n"
            f"{BLUE}Your current context window is unchanged; the savings "
            f"take effect{RESET}\n"
            f"{BLUE}next time this session is resumed (quit, then "
            f"{CODE}claude -r {session_id}{RESET}{BLUE}).{RESET}"
        )

    # Preview path (bare '>trim' or '>trim <options>')
    if not transcript_path:
        return (
            f"{YELLOW}No transcript path available for this session "
            f"yet - try again after the next message.{RESET}"
        )

    if arg:
        opts, err = parse_trim_args(arg)
        if err:
            return (
                f"{YELLOW}{err}{RESET}\n\n" + _trim_usage_lines() + "\n"
                f"{BLUE}Then {CODE}>trim yes{RESET}{BLUE} to apply, "
                f"{CODE}>trim cancel{RESET}{BLUE} to abandon.{RESET}"
            )
    else:
        opts = {"threshold": None, "trim_assistant": None, "tools": []}

    result, err = run_trim_cli(transcript_path, opts, dry_run=True)
    if err:
        return f"{YELLOW}Trim preview failed:{RESET}\n{BLUE}{err}{RESET}"

    if result.get("nothing_to_trim"):
        return (
            f"{BLUE}Session is already lean - only "
            f"~{result.get('tokens_saved', 0):,} tokens would be saved "
            f"({describe_trim_opts(opts)}).{RESET}\n"
            f"{BLUE}Nothing to do.{RESET}"
            + (
                ""
                if arg
                else "\n\n" + _trim_usage_lines()
            )
        )

    save_err = _save_trim_state(
        session_id,
        {
            "created_at": time.time(),
            "transcript_path": transcript_path,
            "opts": opts,
            "preview_tokens": result.get("tokens_saved", 0),
        },
    )
    if save_err:
        return (
            f"{YELLOW}Could not save the trim preview state - nothing "
            f"was armed.{RESET}\n{BLUE}{save_err}{RESET}"
        )

    message = (
        f"{BLUE}Trim preview - {describe_trim_opts(opts)}:{RESET}\n"
        f"{GREEN}  ~{result.get('tokens_saved', 0):,} tokens would be "
        f"saved ({result.get('chars_saved', 0):,} chars){RESET}\n"
        f"{BLUE}  {result.get('num_tools_trimmed', 0)} tool results + "
        f"{result.get('num_assistant_trimmed', 0)} assistant messages "
        f"trimmed{RESET}\n"
        f"{BLUE}  File size: {_fmt_size(result.get('size_before'))} -> "
        f"{_fmt_size(result.get('size_after'))}{RESET}\n\n"
        f"{BLUE}Apply:  {CODE}>trim yes{RESET}    "
        f"{BLUE}Cancel:  {CODE}>trim cancel{RESET}"
    )
    if not arg:
        message += "\n\n" + _trim_usage_lines()
    message += (
        f"\n\n{BLUE}(Trims this session's file in place; a timestamped "
        f".bak backup is kept.{RESET}\n"
        f"{BLUE}Savings apply from the next resume of this "
        f"session.){RESET}"
    )
    return message


def main():
    try:
        data = json.load(sys.stdin)
        # stdin fields are hostile: only well-shaped strings pass.
        session_id = data.get("session_id", "")
        if not isinstance(session_id, str):
            session_id = ""
        transcript_path = data.get("transcript_path", "")
        if not isinstance(transcript_path, str):
            transcript_path = ""
        prompt = data.get("prompt")

        # Fail safe: prompt must be a non-empty string
        if not isinstance(prompt, str) or not prompt.strip():
            sys.exit(0)

        stripped = prompt.strip()
        prompt_lower = stripped.lower()

        # Check triggers with strict matching:
        # Must be exact match OR the trigger followed by a space
        is_resume_trigger = any(
            prompt_lower == t or prompt_lower.startswith(t + " ")
            for t in RESUME_TRIGGERS
        )
        is_session_id_trigger = any(
            prompt_lower == t or prompt_lower.startswith(t + " ")
            for t in SESSION_ID_TRIGGERS
        )
        is_trim_trigger = (
            prompt_lower == TRIM_TRIGGER
            or prompt_lower.startswith(TRIM_TRIGGER + " ")
        )

        if not (
            is_resume_trigger or is_session_id_trigger or is_trim_trigger
        ):
            # Not our trigger, let it pass through
            sys.exit(0)

        if not session_id:
            # No session ID available
            result = {
                "decision": "block",
                "reason": "No session ID available.",
            }
            print(json.dumps(result))
            sys.exit(0)

        if is_trim_trigger:
            # The session id is embedded in the pending-state filename;
            # refuse anything not filename-safe BEFORE any other work.
            if not _is_safe_session_id(session_id):
                result = {
                    "decision": "block",
                    "reason": (
                        "Session ID looks invalid; not touching "
                        "trim state."
                    ),
                }
                print(json.dumps(result))
                sys.exit(0)
            # Args keep their original case (tool names are lowercased
            # during parsing; keywords are matched case-insensitively)
            raw_arg = stripped[len(TRIM_TRIGGER):]
            message = handle_trim(session_id, transcript_path, raw_arg)
        else:
            # Copy session ID and get formatted message
            message = copy_session_id_and_format_message(
                session_id,
                show_resume_instructions=is_resume_trigger,
            )

        # Block the prompt and show the message
        result = {
            "decision": "block",
            "reason": message,
        }
        print(json.dumps(result))
        sys.exit(0)

    except Exception:
        # Any error = pass through silently (fail safe)
        sys.exit(0)


if __name__ == "__main__":
    main()
