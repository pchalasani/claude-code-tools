"""Tests for agent-tunnel CLI helpers + config front-end selection.

Pure / offline: no network, no live tokens, no real state — any config is a
temp TOML and state/registry paths never touch ~/.local/state.
"""

from __future__ import annotations

import pytest

from claude_code_tools.agent_tunnel.cli import _reach_check
from claude_code_tools.agent_tunnel.config import load_config


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


# -- load_config: validate only the ACTIVE front-end's channels (Codex P2) ----


def test_load_config_validates_only_active_front_end(tmp_path) -> None:
    # chat=slack with a valid [slack] table must NOT be rejected by a stale,
    # mistyped [discord] table for the inactive front-end.
    p = tmp_path / "config.toml"
    p.write_text(
        '[tunnel]\nchat = "slack"\n'
        '[slack]\nchannel_ids = ["C123"]\n'
        '[discord]\nchannel_ids = ["123"]\n',  # strings: wrong for discord
        encoding="utf-8",
    )
    cfg = load_config(path=p)  # must not raise — discord table is inactive
    assert cfg.chat == "slack" and cfg.slack.channel_ids == ["C123"]


def test_load_config_rejects_active_front_end_bad_channels(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        '[discord]\nchannel_ids = ["123"]\n',  # strings: wrong for discord
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="discord.*integers"):
        load_config(path=p)  # default chat=discord -> the active table is bad


def test_load_config_chat_override_selects_validation(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        "[slack]\nchannel_ids = [123]\n",  # ints: wrong for slack
        encoding="utf-8",
    )
    # default chat=discord -> the [slack] table is inactive -> OK
    assert load_config(path=p).chat == "discord"
    # but --chat slack makes it active -> validated -> rejected
    with pytest.raises(ValueError, match="slack.*strings"):
        load_config(path=p, chat="slack")


def test_serve_missing_slack_extra_is_clickexception(tmp_path) -> None:
    # With the `slack` extra absent, `serve --chat slack` must give the install
    # hint (ClickException), not a raw ModuleNotFoundError traceback (Codex P2).
    try:
        import slack_bolt  # type: ignore  # noqa: F401

        pytest.skip("slack_bolt installed; this covers the missing-dep path")
    except ImportError:
        pass
    from click.testing import CliRunner

    from claude_code_tools.agent_tunnel.cli import cli

    p = tmp_path / "config.toml"
    p.write_text(
        '[tunnel]\nchat = "slack"\n'
        f'state_path = "{tmp_path}/state.json"\n'
        f'registry_path = "{tmp_path}/registry.json"\n'
        '[slack]\nchannel_ids = ["C1"]\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["serve", "--config", str(p)])
    assert result.exit_code != 0
    assert "slack_bolt is required" in result.output
    assert "claude-code-tools[slack]" in result.output
