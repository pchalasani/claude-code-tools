"""discord_bot pure helpers (importable without discord.py installed)."""

from __future__ import annotations

from claude_code_tools.agent_tunnel.discord_bot import format_relayed_message


def test_format_relayed_message_prefixes_sender() -> None:
    out = format_relayed_message("Chetan", "what is 2+2?")
    assert out == "Chetan (via Discord) says:\nwhat is 2+2?"


def test_format_relayed_message_blank_sender_falls_back() -> None:
    out = format_relayed_message("   ", "hi")
    assert out.startswith("A teammate (via Discord) says:\n")


def test_format_relayed_message_platform_configurable() -> None:
    # A future Slack bot passes its own platform name.
    out = format_relayed_message("Bo", "hi", platform="Slack")
    assert out == "Bo (via Slack) says:\nhi"
