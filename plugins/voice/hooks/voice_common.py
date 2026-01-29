#!/usr/bin/env python3
"""
Shared voice plugin utilities and constants.
"""

from pathlib import Path


def get_voice_config() -> tuple[bool, str, str]:
    """Read voice config from ~/.claude/voice.local.md

    Returns:
        Tuple of (enabled, voice, custom_prompt)
    """
    config_file = Path.home() / ".claude" / "voice.local.md"

    if not config_file.exists():
        return True, "azelma", ""

    content = config_file.read_text()

    enabled = True
    voice = "azelma"
    custom_prompt = ""

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

    return enabled, voice, custom_prompt


def build_full_reminder(custom_prompt: str = "") -> str:
    """Build the full voice reminder for UserPromptSubmit hook."""
    reminder = (
        "Voice feedback is enabled. At the end of your response:\n"
        "- If ≤25 words of natural speakable text, no summary needed\n"
        "- If ≤25 words but contains code/paths/technical output, ADD a 📢 summary\n"
        "- If longer, end with: 📢 [spoken summary, max 25 words]\n\n"
        "VOICE SUMMARY STYLE:\n"
        "- Match the user's tone - if they're casual or use colorful language, "
        "mirror that\n"
        "- Keep it brief and conversational, like you're speaking to them\n"
        "- NEVER include file paths, UUIDs, hashes, or technical identifiers - "
        "use natural language instead (e.g., 'the config file' not "
        "'/Users/foo/bar/config.json')"
    )

    if custom_prompt:
        reminder += (
            f"\n\nCUSTOM VOICE INSTRUCTION (overrides above instructions if they "
            f"contradict): {custom_prompt}"
        )

    return reminder


def build_short_reminder() -> str:
    """Build a brief voice reminder for PostToolUse hook."""
    return (
        "[Voice feedback: when done, end with 📢 summary (max 25 words) "
        "if response is >25 words or contains code/paths]"
    )
