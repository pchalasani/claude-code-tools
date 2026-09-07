"""Isolated real executable checks for environment preflight."""

import json
from pathlib import Path

from claude_code_tools.transfer_environment import inspect_environment


def test_real_probe_and_secret_redaction(tmp_path: Path, monkeypatch) -> None:
    """Run fake native executables, never the user's agents or hooks."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "claude": """#!/bin/sh
if [ "$1" = --version ]; then echo '2.1.263 (Claude Code)'; else
 echo '[{"id":"example@market","enabled":true,"token":"TOPSECRET"}]'; fi
""",
        "python3": '#!/bin/sh\necho "Python 3.9.6"\n',
        "bash": "#!/bin/sh\nexit 42\n",
    }.items():
        executable = bindir / name
        executable.write_text(body)
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir))
    home = tmp_path / "account"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "apiKey": "TOPSECRET",
                "enabledPlugins": {"example@market": True},
                "permissions": {"deny": ["Skill(private-tool)", "Bash(secret)"]},
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {"command": "bash /missing/script.py --token TOPSECRET"}
                            ]
                        }
                    ]
                },
            }
        )
    )
    result = inspect_environment("claude", home)
    assert result["ran"] and result["ok"]
    checks = result["checks"]
    assert checks["native_plugins"]["ran"] and checks["native_plugins"]["ok"]
    assert checks["native_plugins"]["count"] == 1
    assert checks["hook_python"]["version"] == "3.9.6"
    assert not checks["hook_python"]["ok"]
    assert checks["configured_hooks"][0]["missing_script_paths"] == 1
    assert not checks["configured_hooks"][0]["runtime_executed"]
    assert "TOPSECRET" not in json.dumps(result)
    assert checks["skill_overrides"] == ["Skill(private-tool)"]


def test_missing_cli_and_bad_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "nonexistent"))
    (tmp_path / "settings.json").write_text("{broken")
    result = inspect_environment("claude", tmp_path)
    assert result["ran"]
    assert not result["checks"]["native_cli"]["ok"]
    assert not result["checks"]["configuration"]["ok"]
    assert result["remediation"]


def test_missing_account_never_runs_plugin_listing(tmp_path: Path, monkeypatch) -> None:
    """A listing that would create its account must not run for a dry-run target."""
    executable = tmp_path / "claude"
    executable.write_text("""#!/bin/sh
if [ "$1" = --version ]; then echo 2.1.263; exit 0; fi
/bin/mkdir -p "$CLAUDE_CONFIG_DIR"
echo '[]'
""")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    home = tmp_path / "absent"
    result = inspect_environment("claude", home)
    assert not home.exists()
    assert result["checks"]["native_plugins"]["ran"] is False


def test_existing_account_native_writes_are_isolated(
    tmp_path: Path, monkeypatch
) -> None:
    """Even a native version command writing preferences cannot mutate the account."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "claude"
    executable.write_text("""#!/bin/sh
printf changed > "$CLAUDE_CONFIG_DIR/settings.json"
if [ "$1" = --version ]; then echo 2.1.263; else echo '[]'; fi
""")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir))
    home = tmp_path / "profile"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text("{}")
    result = inspect_environment("claude", home)
    assert result["checks"]["native_plugins"]["ran"]
    assert settings.read_text() == "{}"


def test_native_empty_plugin_collection_is_recognized(
    tmp_path: Path, monkeypatch
) -> None:
    """A successful empty native collection differs from an unknown result schema."""
    executable = tmp_path / "codex"
    home = tmp_path / "account"
    home.mkdir()
    monkeypatch.setenv("PATH", str(tmp_path))
    for payload, expected in [
        ({"installed": [], "available": []}, True),
        ({"installed": [{"unknown_field": True}], "available": []}, False),
    ]:
        executable.write_text(
            '#!/bin/sh\nif [ "$1" = --version ]; then echo 0.153.4; else\n'
            + "echo '"
            + json.dumps(payload)
            + "'\nfi\n"
        )
        executable.chmod(0o700)
        result = inspect_environment("codex", home)
        assert result["checks"]["native_plugins"]["ran"] is True
        assert result["checks"]["native_plugins"]["ok"] is expected


def test_environment_comparison_distinguishes_unknown_from_match() -> None:
    from claude_code_tools.transfer_environment import compare_environments

    def environment(enabled: bool) -> dict:
        return {
            "checks": {
                "native_plugins": {
                    "ran": True,
                    "ok": True,
                    "registrations": [{"id": "example@market", "enabled": enabled}],
                }
            }
        }

    assert compare_environments(environment(True), environment(True))["ok"]
    difference = compare_environments(environment(True), environment(False))
    assert difference["ran"] and not difference["ok"]
    assert difference["missing_or_disabled_plugins"] == ["example@market"]
    unknown = compare_environments(environment(True), {})
    assert not unknown["ran"] and not unknown["ok"]
