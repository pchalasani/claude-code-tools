#!/usr/bin/env python3
"""
Shared voice plugin utilities and constants.
"""

from pathlib import Path

# Word limit for short response detection and fallback truncation.
# Explicit summaries (📢 marker or headless Claude) are allowed up to
# MAX_SPOKEN_WORDS * 2 as a safety cap in case the model overshoots.
MAX_SPOKEN_WORDS = 25
MAX_EXPLICIT_SUMMARY_WORDS = MAX_SPOKEN_WORDS * 2


def get_voice_config() -> tuple[bool, str, str, bool]:
    """Read voice config from ~/.claude/voice.local.md

    Returns:
        Tuple of (enabled, voice, custom_prompt, just_disabled)
    """
    config_file = Path.home() / ".claude" / "voice.local.md"

    if not config_file.exists():
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


def clear_just_disabled_flag() -> None:
    """Remove the just_disabled flag from config file."""
    config_file = Path.home() / ".claude" / "voice.local.md"

    if not config_file.exists():
        return

    content = config_file.read_text()
    lines = content.split("\n")
    new_lines = []
    in_frontmatter = False

    for line in lines:
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            new_lines.append(line)
            continue
        if in_frontmatter and line.startswith("just_disabled:"):
            continue  # Skip this line
        new_lines.append(line)

    config_file.write_text("\n".join(new_lines))


def build_full_reminder(custom_prompt: str = "") -> str:
    """Build the full voice reminder for UserPromptSubmit hook."""
    reminder = (
        "Voice feedback is enabled. At the end of your response:\n"
        f"- If ≤{MAX_SPOKEN_WORDS} words of natural speakable text, no summary needed\n"
        f"- If ≤{MAX_SPOKEN_WORDS} words but contains code/paths/technical output, "
        "ADD a 📢 summary\n"
        f"- If longer, end with: 📢 [brief spoken summary, ≤{MAX_EXPLICIT_SUMMARY_WORDS} words]\n\n"
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
        f"[Voice feedback: when done, end with 📢 summary "
        f"(≤{MAX_EXPLICIT_SUMMARY_WORDS} words) "
        f"if response is >{MAX_SPOKEN_WORDS} words or contains code/paths]"
    )
