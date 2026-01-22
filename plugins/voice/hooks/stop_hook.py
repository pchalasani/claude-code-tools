#!/usr/bin/env python3
"""
Stop hook - auto-generate voice summary using headless Haiku.

Instead of blocking and asking Claude to provide a summary (which shows as
"Stop hook error"), this hook:
1. Reads the last assistant message from the session file
2. Calls headless Claude (Haiku) to generate a 1-sentence summary
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


def get_last_assistant_message(session_file: Path) -> str | None:
    """Extract the text of the last assistant message from a session file."""
    last_msg = None

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    if data.get("type") != "assistant":
                        continue

                    message = data.get("message", {})
                    content = message.get("content", "")

                    text = None
                    if isinstance(content, str):
                        text = content.strip()
                    elif isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                        text = " ".join(text_parts).strip()

                    if text:
                        last_msg = text

                except json.JSONDecodeError:
                    continue

    except Exception:
        pass

    return last_msg


def summarize_with_haiku(text: str) -> str | None:
    """Use headless Claude Haiku to generate a 1-sentence summary."""
    if len(text) > 4000:
        text = text[:4000] + "..."

    prompt = f"""Summarize what was accomplished in 1-2 short sentences for a voice update.
Be conversational and concise. Focus on the outcome, not the process.
If the text is casual or uses colorful language, mirror that tone.

Text to summarize:
{text}

Summary:"""

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--model", "haiku",
                "--output-format", "text",
                "--no-session-persistence",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout.strip()

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

    # Get last assistant message
    last_msg = get_last_assistant_message(session_file)
    if not last_msg:
        print(json.dumps({"decision": "approve"}))
        return

    # Generate summary with Haiku
    summary = summarize_with_haiku(last_msg)
    if not summary:
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
