"""Tests for agent-tunnel CLI helpers (pure, no network, no live config)."""

from __future__ import annotations

from claude_code_tools.agent_tunnel.cli import _reach_check


def test_reach_check_channels_ready() -> None:
    ok, label = _reach_check(["C1"], False)
    assert ok is True and "C1" in label


def test_reach_check_dm_only_ready() -> None:
    # DM-only (respond_to_dms, no channels) is a valid serve config, so doctor
    # must pass this readiness check (Codex P3).
    ok, label = _reach_check([], True)
    assert ok is True and "DMs only" in label


def test_reach_check_neither_not_ready() -> None:
    ok, label = _reach_check([], False)
    assert ok is False and "none set" in label
