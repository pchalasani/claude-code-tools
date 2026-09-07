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
