#!/usr/bin/env python3
"""
Stop hook for voice feedback plugin.

When the Claude Code agent stops, this hook:
1. Gets the agent's final output/transcript
2. Uses Haiku to summarize it into 1-2 short sentences
3. Speaks the summary using the say script
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def get_plugin_root() -> Path:
    """Get the plugin root directory."""
    return Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).parent.parent))


def summarize_with_haiku(text: str, max_chars: int = 2000) -> str | None:
    """
    Use Haiku to summarize text into 1-2 short spoken sentences.

    Args:
        text: The text to summarize.
        max_chars: Maximum characters to send to Haiku (to avoid token limits).

    Returns:
        Summary string, or None if summarization failed.
    """
    # Truncate if needed
    if len(text) > max_chars:
        text = text[:max_chars] + "... [truncated]"

    prompt = f"""Summarize the following agent output into 1-2 SHORT sentences
suitable for spoken audio feedback. Be concise and conversational.
Focus on what was accomplished or the key outcome.
Do not use markdown, code blocks, or special formatting.
Just output the plain text summary, nothing else.

Agent output:
{text}"""

    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                "--model", "haiku",
                "--no-session-persistence",
                "--output-format", "text",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"Error running Haiku: {e}", file=sys.stderr)
        return None


def speak(text: str, voice: str | None = None) -> bool:
    """
    Speak text using the say script.

    Args:
        text: Text to speak.
        voice: Optional voice to use.

    Returns:
        True if successful, False otherwise.
    """
    say_script = get_plugin_root() / "scripts" / "say"

    cmd = [str(say_script)]
    if voice:
        cmd.extend(["--voice", voice])
    cmd.append(text)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minutes max for TTS + playback
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"Error running say script: {e}", file=sys.stderr)
        return False


def main():
    """Main hook entry point."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # No valid JSON input, exit silently
        sys.exit(0)

    # Debug: log received data to see what fields are available
    import logging
    logging.basicConfig(filename="/tmp/voice-feedback-debug.log", level=logging.DEBUG)
    logging.debug(f"Stop hook received: {json.dumps(data, indent=2)}")

    # Extract relevant information from Stop event
    # Known fields: session_id, transcript_path, cwd, etc.
    transcript_path = data.get("transcript_path", "")

    # Get directory from transcript_path to find most recent transcript
    text_to_summarize = ""

    if transcript_path:
        transcript_dir = os.path.dirname(transcript_path)
        try:
            # Find most recent .jsonl file in the directory
            import glob
            jsonl_files = glob.glob(os.path.join(transcript_dir, "*.jsonl"))
            if jsonl_files:
                # Sort by modification time, get most recent
                most_recent = max(jsonl_files, key=os.path.getmtime)
                logging.debug(f"Reading most recent transcript: {most_recent}")
                with open(most_recent, "r") as f:
                    lines = f.readlines()
                    # Get last 20 lines of the JSONL (each line is a message)
                    recent_lines = lines[-20:] if len(lines) > 20 else lines
                    # Parse JSONL and extract assistant messages
                    assistant_content = []
                    for line in recent_lines:
                        try:
                            msg = json.loads(line)
                            if msg.get("type") == "assistant":
                                content = msg.get("message", {}).get("content", [])
                                for block in content:
                                    if block.get("type") == "text":
                                        assistant_content.append(block.get("text", ""))
                        except json.JSONDecodeError:
                            continue
                    if assistant_content:
                        # Get last few assistant outputs
                        text_to_summarize = "\n".join(assistant_content[-3:])
        except (IOError, OSError) as e:
            logging.debug(f"Failed to read transcript: {e}")

    if not text_to_summarize:
        # Nothing to summarize, use a generic message
        text_to_summarize = "The task has been completed."

    # Summarize with Haiku
    summary = summarize_with_haiku(text_to_summarize)

    if not summary:
        # Fallback if summarization fails
        summary = "Task completed."

    # Speak the summary
    speak(summary)

    # Output success (Stop hooks typically don't need to return anything specific)
    print(json.dumps({"decision": "approve"}))
    sys.exit(0)


if __name__ == "__main__":
    main()
