"""Package-level contract for the paired msg plugins."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_code_tools.amux.scan import resolve_pane_agent
from claude_code_tools.msg.models import (
    AgentKind,
    ConsumerProtocol,
    RegistrationIdentity,
)
from claude_code_tools.msg.store import MsgStore
from claude_code_tools.process_identity import process_start_identity
from tests.test_msg_lifecycle_subprocess import disposable_tmux

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/msg"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def plugin_payload_sha256() -> str:
    digest = hashlib.sha256()
    files = sorted(
        path for path in PLUGIN.rglob("*")
        if path.is_file()
        and path.name != "release-evidence.json"
        and "__pycache__" not in path.parts
    )
    for path in files:
        relative = path.relative_to(PLUGIN).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def test_paired_manifests_share_version_and_codex_hook_path():
    claude = load_json(PLUGIN / ".claude-plugin/plugin.json")
    codex = load_json(PLUGIN / ".codex-plugin/plugin.json")

    assert claude["version"] == codex["version"] == "1.15.0"
    assert codex["hooks"] == "./hooks/hooks.json"
    assert "Native lifecycle hooks" in codex["interface"]["capabilities"]


def test_plugin_hook_file_uses_installed_root_for_all_native_events():
    hooks = load_json(PLUGIN / "hooks/hooks.json")["hooks"]

    assert set(hooks) == {"PostToolUse", "Stop", "UserPromptSubmit"}
    for event, groups in hooks.items():
        assert len(groups) == 1
        command = groups[0]["hooks"][0]["command"]
        assert "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/hooks/msg_hook.py" in command
        assert {
            "PostToolUse": "post-tool-use",
            "Stop": "stop",
            "UserPromptSubmit": "prompt-submit",
        }[event] in command


def test_plugin_hook_wrapper_invokes_installed_adapter():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "hooks/msg_hook.py"), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "post-tool-use" in result.stdout
    assert "prompt-submit" in result.stdout


def test_both_marketplaces_expose_updated_msg_metadata():
    claude = load_json(ROOT / ".claude-plugin/marketplace.json")
    codex = load_json(ROOT / ".agents/plugins/marketplace.json")
    claude_msg = next(item for item in claude["plugins"] if item["name"] == "msg")
    codex_msg = next(item for item in codex["plugins"] if item["name"] == "msg")

    assert "native First-mate lifecycle hooks" in claude_msg["description"]
    assert codex_msg["interface"]["displayName"] == "Msg"
    assert "native lifecycle hooks" in codex_msg["interface"]["shortDescription"]


def test_release_evidence_binds_cli_contract_plugin_version_and_tree_hash():
    evidence = load_json(PLUGIN / "release-evidence.json")
    claude = PLUGIN / ".claude-plugin/plugin.json"
    codex = PLUGIN / ".codex-plugin/plugin.json"

    assert evidence["schema"] == "msg.plugin.release.v1"
    assert evidence["cli_contract_schema"] == "msg.cli.v1"
    assert evidence["cli_release_base"] == "1.25.6"
    assert evidence["cli_release_status"] == "unreleased"
    assert evidence["plugin_version"] == "1.15.0"
    assert evidence["plugin_payload_sha256"] == plugin_payload_sha256()
    assert evidence["claude_manifest_sha256"] == hashlib.sha256(
        claude.read_bytes()
    ).hexdigest()
    assert evidence["codex_manifest_sha256"] == hashlib.sha256(
        codex.read_bytes()
    ).hexdigest()


def test_installed_root_hook_commands_drive_real_pane_state(tmp_path):
    hooks = load_json(PLUGIN / "hooks/hooks.json")["hooks"]
    commands = {
        event: groups[0]["hooks"][0]["command"]
        for event, groups in hooks.items()
    }
    with disposable_tmux(tmp_path, ("codex",)) as (socket_path, session, panes):
        pane = panes[0]
        target = resolve_pane_agent(pane, str(socket_path))
        assert target is not None
        db_dir = tmp_path / "msg-state"
        store = MsgStore(db_dir / "msg.db")
        sender = store.register_agent("sender", "%99", session, AgentKind.CLAUDE)
        receiver = store.register_agent(
            "receiver", pane, session, AgentKind.CODEX, str(socket_path),
            display_addr=target.pane,
            pid=target.pid,
            cwd=target.cwd,
            consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
            process_start_identity=process_start_identity(target.pid),
        )
        thread = store.create_thread(
            "hook", sender.session_id, [sender.session_id, receiver.session_id],
        )
        store.send_message(thread.id, sender.session_id, "pending")
        identity = RegistrationIdentity.from_agent(receiver)
        store.set_continuation(
            identity,
            "generation-1",
            ttl_secs=1,
            now=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{Path(sys.executable).parent}:{env.get('PATH', '')}",
                "PYTHONPATH": str(ROOT),
                "PLUGIN_ROOT": str(PLUGIN),
                "MSG_DB_DIR": str(db_dir),
                "TMUX": f"{socket_path},0,0",
                "TMUX_PANE": pane,
            }
        )

        post = subprocess.run(
            ["bash", "-c", commands["PostToolUse"]],
            input="{}",
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert post.returncode == 0
        assert store.get_continuation_status(receiver.session_id).state.value == (
            "active_fresh"
        )

        stop = subprocess.run(
            ["bash", "-c", commands["Stop"]],
            input=json.dumps({"model": "gpt-test", "stop_hook_active": False}),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert json.loads(stop.stdout)["decision"] == "block"

        prompt = subprocess.run(
            ["bash", "-c", commands["UserPromptSubmit"]],
            input=json.dumps({"prompt": "opaque-user-prompt"}),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        context = json.loads(prompt.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        assert "$first-mate" in context
        assert "opaque-user-prompt" not in prompt.stdout
        assert store.get_inbox(receiver.session_id)[0]["state"] == "pending"
