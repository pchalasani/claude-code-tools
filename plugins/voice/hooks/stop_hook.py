#!/usr/bin/env python3
"""
Stop hook - auto-generate voice summary using headless Claude.

Instead of blocking and asking Claude to provide a summary (which shows as
"Stop hook error"), this hook:
1. Reads the last assistant message from the session file
2. Calls headless Claude to generate a 1-sentence summary
3. Calls the say script to speak it
4. Returns approve (no blocking, no error display)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent


def get_voice_config() -> tuple[bool, str]:
    """Read voice config from ~/.claude/voice.local.md"""
    config_file = Path.home() / ".claude" / "voice.local.md"

    if not config_file.exists():
        config_file.write_text("""---
voice: azelma
enabled: true
---

# Voice Feedback Configuration

Use `/voice:speak stop` to disable, `/voice:speak <name>` to change voice.
""")
        return True, "azelma"

    content = config_file.read_text()

    enabled = True
    voice = "azelma"

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

    return enabled, voice


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
        return " ".join(text_parts).strip()
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


def summarize_with_claude(conversation: list[tuple[str, str]]) -> str | None:
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

    prompt = f"""PAST CONVERSATION (for tone context):
{past_text}

---

LAST ASSISTANT MESSAGE:
{last_assistant_msg}

---

Summarize the LAST ASSISTANT MESSAGE as a 1-2 sentence voice update. Keep it short.

Guidelines:
1. Use first person ("I", "I'm", "I'll")
2. Don't narrate - rephrase what's being said or asked
3. Phrase as a wrap-up or handoff statement
4. No meta-commentary ("I explained", "I gave you", "I told you") - just state the content directly
5. IMPORTANT: Match the user's tone - if they're casual or use colorful language, mirror that. Keep it to 1-2 sentences max.
6. If the message is an error, exception, or doesn't look like normal text, just say "Ran into an issue - see the output."
7. This will be SPOKEN aloud via TTS - avoid file paths, UUIDs, hashes, or technical identifiers. Use natural language instead (e.g., "the config file" not "/Users/foo/bar/config.json")."""

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
            return data.get("result", "").strip()

    except Exception:
        pass

    return None


def speak_summary(session_id: str, summary: str, voice: str) -> None:
    """Call the say script to speak the summary (runs in background)."""
    say_script = PLUGIN_ROOT / "scripts" / "say"

    try:
        # Run in background so we can return JSON immediately
        subprocess.Popen(
            [
                str(say_script),
                "--session", session_id,
                "--voice", voice,
                summary,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "approve"}))
        return

    session_id = data.get("session_id", "")

    # Check if voice is enabled
    enabled, voice = get_voice_config()
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

    # Get recent conversation for context
    conversation = get_recent_conversation(session_file)
    if not conversation:
        print(json.dumps({"decision": "approve"}))
        return

    # Generate summary with Claude
    summary = summarize_with_claude(conversation)
    if not summary:
        # Fallback: use last message text
        last_msg = conversation[-1][1] if conversation else ""
        summary = last_msg[:100] + ("..." if len(last_msg) > 100 else "")

    # Speak it
    speak_summary(session_id, summary, voice)

    # Approve with systemMessage to display the summary
    print(json.dumps({
        "decision": "approve",
        "systemMessage": f"🔊 {summary}"
    }))


if __name__ == "__main__":
    main()
