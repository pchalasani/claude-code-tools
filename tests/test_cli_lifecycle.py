"""agent-tunnel CLI handle/thread lifecycle: `revoke` + `forget --handle`.

Offline: CliRunner with a temp config whose registry/state paths are pinned to
tmp_path — no network, no daemon, no real ~/.local/state.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from claude_code_tools.agent_tunnel.cli import cli
from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry
from claude_code_tools.agent_tunnel.store import TunnelStore


def _cfg(tmp_path: Path) -> Path:
    """A temp config.toml pinning registry/state to tmp_path."""
    p = tmp_path / "config.toml"
    p.write_text(
        f'[tunnel]\nregistry_path = "{tmp_path}/registry.json"\n'
        f'state_path = "{tmp_path}/state.json"\n',
        encoding="utf-8",
    )
    return p


def test_revoke_cli_unpublishes_handle(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    reg = tmp_path / "registry.json"
    Registry(reg).upsert(PublishRecord(handle="pay", session_id="S1", cwd="/p"))
    r = CliRunner().invoke(cli, ["revoke", "pay", "--config", str(cfg)])
    assert r.exit_code == 0, r.output
    assert Registry(reg).get("pay") is None  # hidden after revoke
    # unknown handle -> clean ClickException
    r2 = CliRunner().invoke(cli, ["revoke", "nope", "--config", str(cfg)])
    assert r2.exit_code != 0 and "nope" in r2.output


def test_forget_handle_drops_all_threads_of_handle(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    state = tmp_path / "state.json"
    store = TunnelStore(state)
    store.bind("th:1", "pay", "S1", "/p", "headless")
    store.bind("th:2", "pay", "S1", "/p", "headless")
    store.bind("th:3", "ops", "S2", "/p", "headless")
    r = CliRunner().invoke(
        cli, ["forget", "--handle", "pay", "--config", str(cfg)]
    )
    assert r.exit_code == 0, r.output
    s = TunnelStore(state)
    assert s.get("th:1") is None and s.get("th:2") is None  # both pay threads
    assert s.get("th:3") is not None  # ops thread untouched


def test_forget_requires_exactly_one_target(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    r = CliRunner().invoke(
        cli, ["forget", "--handle", "pay", "--all", "--config", str(cfg)]
    )
    assert r.exit_code != 0 and "exactly one" in r.output
