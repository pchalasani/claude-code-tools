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
    assert difference["runtime_parity_verified"] is False
    assert difference["missing_or_disabled_plugins"] == ["example@market"]
    unknown = compare_environments(environment(True), {})
    assert not unknown["ran"] and not unknown["ok"]


def test_codex_git_marketplace_probe_and_incomplete_cache(
    tmp_path: Path, monkeypatch
) -> None:
    """Snapshot git sources resolve locally; auth-dependent cache stays unverified."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "codex"
    cli.write_text("""#!/bin/sh
if [ "$1" = --version ]; then echo 0.153.4; exit 0; fi
case "$*" in *marketplaces.fixture.source_type*) ;; *) exit 5;; esac
if [ ! -f "$CODEX_HOME/plugins/cache/fixture/tool/1/.codex-plugin/plugin.json" ]; then exit 6; fi
echo '{"installed":[{"name":"tool","pluginId":"tool@fixture","enabled":true}],"available":[]}'
""")
    cli.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir))
    home = tmp_path / "profile"
    (home / ".tmp/marketplaces/fixture").mkdir(parents=True)
    (home / "config.toml").write_text(
        '[marketplaces.fixture]\nsource_type="git"\nsource="https://example.invalid/repo"\n'
    )
    for marketplace, name in [("fixture", "tool"), ("bundled", "account-only")]:
        manifest = (
            home / "plugins/cache" / marketplace / name / "1/.codex-plugin/plugin.json"
        )
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name, "version": "1"}))
    check = inspect_environment("codex", home)["checks"]["native_plugins"]
    assert check["ran"] and check["command_ok"]
    assert check["registrations"][0]["id"] == "tool@fixture"
    assert not check["complete"] and not check["ok"]
    assert check["cached_not_in_native_probe"] == ["account-only@bundled"]


def test_partial_native_parity_does_not_invent_missing_plugins() -> None:
    from claude_code_tools.transfer_environment import compare_environments

    source = {
        "checks": {
            "native_plugins": {
                "ran": True,
                "ok": False,
                "registrations": [{"id": "visible@market"}],
                "cached_registrations": ["visible@market", "hidden@bundled"],
            }
        }
    }
    target = {
        "checks": {
            "native_plugins": {
                "ran": True,
                "ok": False,
                "registrations": [],
                "cached_registrations": ["hidden@bundled"],
            }
        }
    }
    result = compare_environments(source, target)
    assert not result["runtime_parity_verified"]
    assert result["missing_or_disabled_plugins"] == []
    assert result["static_cache_only_in_source"] == ["visible@market"]


def test_plugin_root_direct_executable_with_spaces(tmp_path: Path, monkeypatch) -> None:
    """A quoted direct plugin script resolves after plugin-root expansion."""
    home = tmp_path / "account"
    root = home / "plugins/cache/test plugin/1"
    script = root / "scripts/pretooluse.sh"
    script.parent.mkdir(parents=True)
    marker = tmp_path / "must-not-run"
    script.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
    script.chmod(0o700)
    hooks = root / "hooks/hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "command": '"${CLAUDE_PLUGIN_ROOT}/scripts/pretooluse.sh"'
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    (home / "plugins/installed_plugins.json").write_text(
        json.dumps({"plugins": {"test@fixture": [{"installPath": str(root)}]}})
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-native-cli"))
    checks = inspect_environment("claude", home)["checks"]["configured_hooks"]
    assert len(checks) == 1
    assert checks[0]["ran"] and checks[0]["ok"]
    assert checks[0]["launcher_available"]
    assert checks[0]["script_paths_checked"] == 1
    assert checks[0]["missing_script_paths"] == 0
    assert not marker.exists()


def test_hook_assignment_prefix_does_not_hide_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    """Leading shell assignments are metadata, never the executable to locate."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "python"
    executable.write_text("#!/bin/sh\nexit 73\n")
    executable.chmod(0o700)
    script = tmp_path / "hook.py"
    script.write_text('raise AssertionError("must not run")\n')
    home = tmp_path / "profile"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "command": f'PYTHONPATH="/some path" MODE=test python "{script}"'
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("PATH", str(bindir))
    checks = inspect_environment("claude", home)["checks"]["configured_hooks"]
    assert len(checks) == 1
    assert checks[0]["ran"] and checks[0]["ok"]
    assert checks[0]["launcher_available"]
    assert checks[0]["script_paths_checked"] == 1
    assert not checks[0]["runtime_executed"]


def test_plugin_hooks_array_reports_warning(tmp_path: Path, monkeypatch) -> None:
    """Valid JSON with a non-object root produces diagnostics instead of a crash."""
    home = tmp_path / "profile"
    plugin = home / "plugins/cache/example/1"
    hooks = plugin / "hooks/hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("[]")
    (home / "plugins/installed_plugins.json").write_text(
        json.dumps({"plugins": {"example@fixture": [{"installPath": str(plugin)}]}})
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-native-cli"))
    report = inspect_environment("claude", home)
    assert report["ran"] and report["ok"]
    assert report["checks"]["configured_hooks"] == []
    assert any(
        "hooks.json must contain an object" in item for item in report["warnings"]
    )


def test_compound_and_builtin_hooks_are_unverified(tmp_path: Path, monkeypatch) -> None:
    """Shell composition is not mistaken for a missing executable or executed."""
    home = tmp_path / "profile"
    home.mkdir()
    marker = tmp_path / "must-not-exist"
    commands = [
        f'cd "{tmp_path}" && python hook.py',
        f'cd "{tmp_path}"&&python hook.py',
        f'echo test > "{marker}"',
        "export EXAMPLE=value",
    ]
    (home / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"hooks": [{"command": command} for command in commands]}
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-native-cli"))
    report = inspect_environment("claude", home)
    checks = report["checks"]["configured_hooks"]
    assert len(checks) == len(commands)
    for check in checks:
        assert check["ran"] is False and check["ok"] is False
        assert "unverified" in check["reason"]
        assert "launcher_available" not in check
        assert check["runtime_executed"] is False
        assert check["remediation"]
    assert not marker.exists()


def test_quoted_bash_wrapper_remains_inspectable(tmp_path: Path, monkeypatch) -> None:
    """Operators inside a quoted bash argument do not become outer composition."""
    home = tmp_path / "profile"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {"command": '/bin/bash -c "echo hello && echo world"'}
                            ]
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-native-cli"))
    checks = inspect_environment("claude", home)["checks"]["configured_hooks"]
    assert len(checks) == 1
    assert checks[0]["ran"] and checks[0]["ok"]
    assert checks[0]["launcher_available"]
    assert checks[0]["runtime_executed"] is False


def test_failed_simple_hooks_surface_sanitized_summary(
    tmp_path: Path, monkeypatch
) -> None:
    """Normal-output warning fields expose failed hooks without leaking commands."""
    home = tmp_path / "profile"
    home.mkdir()
    commands = [
        "nonexistent-hook-launcher --token TOPSECRET",
        "/bin/bash /missing/private-hook.sh --token TOPSECRET",
        '"unterminated TOPSECRET',
    ]
    (home / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"hooks": [{"command": command} for command in commands]}
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-native-cli"))
    result = inspect_environment("claude", home)
    summary = "\n".join(result["warnings"] + result["remediation"])
    assert "3 configured hook check(s) failed" in summary
    assert "install missing launchers" in summary
    assert "missing script paths" in summary
    assert "repair invalid quoting" in summary
    assert "TOPSECRET" not in json.dumps(result)
    assert "nonexistent-hook-launcher" not in summary
    assert "/missing/private-hook.sh" not in summary


def test_added_skill_permission_denies_prevent_parity() -> None:
    """Matching plugins do not imply parity when destination denies another skill."""
    from claude_code_tools.transfer_environment import compare_environments

    native = {"ran": True, "ok": True, "registrations": [{"id": "tool@fixture"}]}
    source = {
        "checks": {"native_plugins": native, "skill_overrides": ["Skill(existing)"]}
    }
    target = {
        "checks": {
            "native_plugins": native,
            "skill_overrides": ["Skill(existing)", "Skill(newly-denied)"],
        }
    }
    result = compare_environments(source, target)
    assert result["ran"]
    assert not result["ok"] and not result["runtime_parity_verified"]
    assert result["newly_denied_skills"] == ["Skill(newly-denied)"]
    assert result["remediation"]
    same = compare_environments(source, source)
    assert same["ok"] and same["newly_denied_skills"] == []


def test_env_hook_wrapper_cannot_prove_interpreter_available(
    tmp_path: Path, monkeypatch
) -> None:
    """An available env binary does not prove its wrapped interpreter exists."""
    home = tmp_path / "profile"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "command": "/usr/bin/env definitely-missing-python hook.py"
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-interpreters"))
    checks = inspect_environment("claude", home)["checks"]["configured_hooks"]
    assert len(checks) == 1
    assert not checks[0]["ran"] and not checks[0]["ok"]
    assert checks[0]["reason"] == "Environment-wrapper hook is unverified"
    assert "launcher_available" not in checks[0]
    assert checks[0]["remediation"]
    assert not checks[0]["runtime_executed"]


def test_relative_hook_script_is_not_verified_from_launcher_only(
    tmp_path: Path, monkeypatch
) -> None:
    """An installed interpreter cannot prove a cwd-relative hook script is present."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "python"
    executable.write_text("#!/bin/sh\nexit 87\n")
    executable.chmod(0o700)
    home = tmp_path / "profile"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"hooks": [{"command": "python missing-hook.py"}]}]
                }
            }
        )
    )
    monkeypatch.setenv("PATH", str(bindir))
    result = inspect_environment("claude", home)
    check = result["checks"]["configured_hooks"][0]
    assert check["ran"] is False and check["ok"] is False
    assert "Relative hook script path" in check["reason"]
    assert check["runtime_executed"] is False
    assert any("actual working directory" in item for item in result["remediation"])
