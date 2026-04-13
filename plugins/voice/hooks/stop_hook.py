#!/usr/bin/env python3
"""
Stop hook - extract or generate voice summary.

This hook tries to extract an inline voice summary (📢 marker) first,
falling back to headless Claude only if no marker is found.

Flow:
1. Look for 📢 marker in the last assistant message (instant, no API call)
2. If not found, call headless Claude to generate a summary (slower fallback)
3. Speak the summary via the say script
4. Return approve (non-blocking)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from voice_common import MAX_SPOKEN_WORDS

PLUGIN_ROOT = Path(__file__).parent.parent


def get_voice_config() -> tuple[bool, str, str, bool]:
    """Read voice config from ~/.claude/voice.local.md

    Returns:
        Tuple of (enabled, voice, custom_prompt, just_disabled)
    """
    config_file = Path.home() / ".claude" / "voice.local.md"

    if not config_file.exists():
        config_file.write_text("""---
voice: azelma
enabled: true
---

# Voice Feedback Configuration

Use `/voice:speak stop` to disable, `/voice:speak <name>` to change voice.
""")
        return True, "azelma", "", False

    content = config_file.read_text()

    enabled = True
    voice = "azelma"
    custom_prompt = ""
    just_disabled = False

    lines = content.split("\n")
    in_frontmatter = False
    for line in lines:
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                break
        if in_frontmatter:
            if line.startswith("enabled:"):
                val = line.split(":", 1)[1].strip()
                enabled = val.lower() != "false"
            elif line.startswith("voice:"):
                voice = line.split(":", 1)[1].strip()
            elif line.startswith("prompt:"):
                # Handle quoted strings
                val = line.split(":", 1)[1].strip()
                # Remove surrounding quotes if present
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                custom_prompt = val
            elif line.startswith("just_disabled:"):
                val = line.split(":", 1)[1].strip()
                just_disabled = val.lower() == "true"

    return enabled, voice, custom_prompt, just_disabled


def find_session_file(session_id: str) -> Path | None:
    """Find session file by ID in ~/.claude/projects/*/"""
    if not session_id:
        return None

    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    projects_dir = claude_home / "projects"

    if not projects_dir.exists():
        return None

    # Search all project directories
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Try exact match first
        exact_path = project_dir / f"{session_id}.jsonl"
        if exact_path.exists():
            return exact_path

        # Try partial match
        for jsonl_file in project_dir.glob(f"*{session_id}*.jsonl"):
            return jsonl_file

    return None


def trim_to_words(text: str, max_words: int) -> str:
    """Trim text to max_words, adding ellipsis if truncated."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def extract_voice_marker(text: str) -> str | None:
    """Extract voice summary from 📢 marker if present.

    Looks for lines starting with 📢 (with optional whitespace).
    Returns the text after the marker, or None if not found.
    """
    # Match 📢 at start of line, capture everything after it
    # Handle both "📢 text" and "📢text" formats
    pattern = r'^[ \t]*📢[ \t]*(.+?)[ \t]*$'
    match = re.search(pattern, text, re.MULTILINE)
    if match:
        summary = match.group(1).strip()
        # Clean up any markdown artifacts (brackets, etc.)
        summary = re.sub(r'^\[|\]$', '', summary)
        return summary if summary else None
    return None


def word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def is_short_response(text: str, max_words: int = MAX_SPOKEN_WORDS) -> bool:
    """Check if response is short enough to speak directly."""
    return word_count(text) <= max_words


def extract_message_text(data: dict) -> str | None:
    """Extract text content from a message data dict."""
    message = data.get("message", {})
    content = message.get("content", "")

    if isinstance(content, str):
        return content.strip()
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        # Join with newlines so 📢 markers stay at line start
        return "\n".join(text_parts).strip()
    return None


def _read_session_messages(session_file: Path) -> tuple[int, int, str | None]:
    """Read session file and extract message ordering info.

    Returns:
        Tuple of (last_user_line, last_assistant_text_line, last_assistant_text)
        - last_user_line: Line number of the most recent user message
        - last_assistant_text_line: Line number of the most recent assistant with text
        - last_assistant_text: text content of that assistant message (if any)
    """
    last_user_line = -1
    last_assistant_text_line = -1
    last_assistant_text = None

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    msg_type = data.get("type")

                    if msg_type == "user":
                        last_user_line = line_num

                    elif msg_type == "assistant":
                        text = extract_message_text(data)
                        if text:
                            last_assistant_text_line = line_num
                            last_assistant_text = text

                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return last_user_line, last_assistant_text_line, last_assistant_text


def get_last_assistant_message(
    session_file: Path,
    max_retries: int = 10,
    retry_delay: float = 0.5,
) -> str | None:
    """Get the last assistant message text, with retry for race conditions.

    The stop hook can fire before the assistant message is written to the session
    file. This checks if the last assistant message with text comes AFTER the
    last user message (by line order). If not, it retries.

    Default: 10 retries × 0.5s = 5 seconds total wait window.
    """
    import time

    for attempt in range(max_retries):
        last_user_line, last_asst_line, last_asst_text = _read_session_messages(
            session_file
        )

        # Valid if: we have text AND the assistant line comes after the user line
        if last_asst_text and last_asst_line > last_user_line >= 0:
            return last_asst_text

        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    return None


def get_recent_conversation(
    session_file: Path,
    num_turns: int = 5,
    max_assistant_words: int = 500,
) -> list[tuple[str, str]]:
    """
    Extract recent conversation turns from session file.

    Returns list of (role, text) tuples, most recent last.
    Assistant messages are trimmed to max_assistant_words.
    """
    messages: list[tuple[str, str]] = []

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    msg_type = data.get("type")

                    if msg_type not in ("user", "assistant"):
                        continue

                    text = extract_message_text(data)
                    if not text:
                        continue

                    # Skip tool results (user messages with tool_result content)
                    if msg_type == "user":
                        content = data.get("message", {}).get("content", [])
                        if isinstance(content, list) and content:
                            if isinstance(content[0], dict):
                                if content[0].get("type") == "tool_result":
                                    continue

                    # Trim assistant messages
                    if msg_type == "assistant":
                        text = trim_to_words(text, max_assistant_words)

                    messages.append((msg_type, text))

                except json.JSONDecodeError:
                    continue

    except Exception:
        pass

    # Return last num_turns * 2 messages (to get num_turns of conversation)
    return messages[-(num_turns * 2):]


def summarize_with_claude(
    conversation: list[tuple[str, str]],
    custom_prompt: str = "",
) -> str | None:
    """Use headless Claude to generate a 1-sentence summary."""
    if not conversation:
        return None

    # Separate past conversation from last assistant message
    last_assistant_msg = None
    past_conv = []

    # Find the last assistant message
    for i in range(len(conversation) - 1, -1, -1):
        role, text = conversation[i]
        if role == "assistant":
            last_assistant_msg = text
            past_conv = conversation[:i]
            break

    if not last_assistant_msg:
        return None

    # Format past conversation for tone context
    past_lines = []
    for role, text in past_conv:
        if len(text) > 500:
            text = text[:500] + "..."
        past_lines.append(f"[{role}]: {text}")

    past_text = "\n\n".join(past_lines) if past_lines else "(no prior context)"

    # Limit sizes
    if len(past_text) > 3000:
        past_text = past_text[-3000:]
    if len(last_assistant_msg) > 2000:
        last_assistant_msg = last_assistant_msg[:2000] + "..."

    # Build the prompt
    base_instruction = "You are the assistant who just wrote that message. Give a brief SPOKEN voice update to the user. Match the user's tone - if they're casual or use colorful language, mirror that. IMPORTANT: Keep it to 1-2 sentences max, and NEVER longer than the original message. Since this will be spoken aloud, avoid file paths, UUIDs, hashes, or technical identifiers - use natural language instead (e.g., 'the config file' not '/Users/foo/bar/config.json'). What would you say?"

    # Append custom prompt if provided
    if custom_prompt:
        base_instruction += f"\n\nAdditional instruction: {custom_prompt}"

    prompt = f"""PAST CONVERSATION (for tone context):
{past_text}

---

YOUR LAST MESSAGE:
{last_assistant_msg}

---

{base_instruction}"""

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--output-format", "json",
                "--no-session-persistence",
                "--setting-sources", "",  # Skip CLAUDE.md files
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            summary = data.get("result", "").strip()
            # No truncation - headless Claude already generates a summary
            return summary

    except Exception:
        pass

    return None


def speak_summary(session_id: str, summary: str, voice: str) -> None:
    """Call the say script to speak the summary (runs in background)."""
    say_script = PLUGIN_ROOT / "scripts" / "say"

    try:
        # On Windows, bash scripts can't be executed directly by subprocess.Popen
        # (WinError 193: not a valid Win32 application). Must invoke through bash.
        import platform
        if platform.system() == "Windows":
            cmd = ["bash", str(say_script), "--session", session_id, "--voice", voice, summary]
        else:
            cmd = [str(say_script), "--session", session_id, "--voice", voice, summary]

        # Run in background so we can return JSON immediately
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "approve"}))
        return

    # DEBUG: Log the full stop hook input to see what data is available
    debug_file = Path("/tmp/voice-stop-hook-input.json")
    try:
        with open(debug_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass

    session_id = data.get("session_id", "")

    # Check if voice is enabled (ignore just_disabled - only used by prompt hook)
    enabled, voice, custom_prompt, _just_disabled = get_voice_config()
    if not enabled:
        print(json.dumps({"decision": "approve"}))
        return

    if not session_id:
        print(json.dumps({"decision": "approve"}))
        return

    # Find the session file
    session_file = find_session_file(session_id)
    if not session_file:
        print(json.dumps({"decision": "approve"}))
        return

    summary = None
    used_headless = False

    # Get last assistant message
    last_assistant_msg = get_last_assistant_message(session_file)

    # Flexible limit for explicit summaries (1.5x the strict limit)
    flexible_limit = int(MAX_SPOKEN_WORDS * 1.5)

    # Strategy 1: Try to extract 📢 marker (instant!)
    if last_assistant_msg:
        marker_summary = extract_voice_marker(last_assistant_msg)
        if marker_summary:
            summary = trim_to_words(marker_summary, flexible_limit)

    # Strategy 2: If no marker but response is short, speak directly
    if not summary and last_assistant_msg:
        if is_short_response(last_assistant_msg, max_words=MAX_SPOKEN_WORDS):
            summary = last_assistant_msg  # Already short enough

    # Strategy 3: Fall back to headless Claude summarization (slower)
    if not summary and last_assistant_msg:
        conversation = get_recent_conversation(session_file)
        if conversation:
            summary = summarize_with_claude(conversation, custom_prompt)
            if summary:
                summary = trim_to_words(summary, flexible_limit)
                used_headless = True

    # Strategy 4: Last resort - truncate last message
    if not summary and last_assistant_msg:
        summary = trim_to_words(last_assistant_msg, MAX_SPOKEN_WORDS)

    if not summary:
        print(json.dumps({"decision": "approve"}))
        return

    # No final truncation - trust explicit summaries (marker or headless Claude)
    # Only Strategy 2 and 4 use raw text which is already bounded by MAX_SPOKEN_WORDS

    # Speak it
    speak_summary(session_id, summary, voice)

    # Only show output if we used headless Claude (summary is new, not in message)
    if used_headless:
        print(json.dumps({
            "decision": "approve",
            "systemMessage": f"🔊 {summary}"
        }))
    else:
        print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
