"""Read-only destination prerequisite checks without running hooks or model calls."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any


def _run(argv: list[str], environment: dict[str, str]) -> dict[str, Any]:
    """Capture a bounded diagnostic without exposing raw stdout or stderr."""
    try:
        result = subprocess.run(
            argv,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ran": False, "ok": False, "error": type(error).__name__}
    return {
        "ran": True,
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "output": result.stdout[:2_000_000],
    }


def _version(argv: list[str], environment: dict[str, str]) -> dict[str, Any]:
    """Return only the version number from a native command."""
    result = _run(argv, environment)
    match = re.search(r"\b\d+\.\d+\.\d+\b", result.pop("output", ""))
    result["version"] = match.group() if match else None
    result["ok"] = result["ok"] and match is not None
    return result


def _registrations(value: Any) -> list[dict[str, Any]]:
    """Extract comparable non-secret identity and enabled fields from native JSON."""
    result: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            result.extend(_registrations(item))
    elif isinstance(value, dict):
        identity = value.get("id") or value.get("name") or value.get("pluginId")
        if isinstance(identity, str) and re.fullmatch(r"[\w@./:-]{1,200}", identity):
            item = {"id": identity}
            for key in ("enabled", "version", "scope"):
                field = value.get(key)
                if isinstance(field, bool) or (
                    isinstance(field, str) and re.fullmatch(r"[\w.@/-]{1,100}", field)
                ):
                    item[key] = field
            result.append(item)
        for key in ("plugins", "installed", "marketplaces", "data"):
            if key in value:
                result.extend(_registrations(value[key]))
    return result


def _inspect_environment(agent: str, home: Path, runtime_home: Path) -> dict[str, Any]:
    """Inspect versions, effective plugin discovery, overrides and hook dependencies.

    Hooks are never executed. Configuration is inspected without returning command
    text, environment values, MCP arguments, or authentication material. A successful
    inspection means the checks ran, not that every prerequisite is satisfied.
    """
    if agent not in {"claude", "codex"}:
        raise ValueError("Agent must be claude or codex")
    environment = dict(os.environ)
    environment["CLAUDE_CONFIG_DIR" if agent == "claude" else "CODEX_HOME"] = str(
        runtime_home
    )
    checks: dict[str, Any] = {}
    warnings: list[str] = []
    remediation: list[str] = []
    executable = shutil.which(agent)
    checks["native_cli"] = {
        "ran": True,
        "ok": executable is not None,
        "path": executable,
    }
    if executable:
        version_environment = dict(environment)
        checks["native_version"] = _version(
            [executable, "--version"], version_environment
        )
        native = (
            _run([executable, "plugin", "list", "--json"], environment)
            if home.is_dir()
            else {"ran": False, "ok": False, "error": "Account not initialized"}
        )
        raw = native.pop("output", "")
        registrations: list[dict[str, Any]] = []
        if native["ok"]:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, (dict, list)):
                    raise ValueError("Unsupported plugin JSON")  # noqa: TRY004
                registrations = _registrations(parsed)
                if parsed and not registrations:
                    raise ValueError("Unrecognized plugin JSON schema")
            except ValueError:
                native["ok"] = False
                native["error"] = "Unrecognized plugin JSON schema"
        native["registrations"] = registrations
        native["count"] = len(registrations)
        checks["native_plugins"] = native
    else:
        remediation.append(f"Install {agent}, then rerun the environment check.")
    python = shutil.which("python3")
    checks["hook_python"] = {"ran": True, "ok": False, "path": python}
    if python:
        checks["hook_python"].update(_version([python, "--version"], environment))
        version = checks["hook_python"].get("version")
        if version and tuple(map(int, version.split("."))) < (3, 11, 0):
            warnings.append("The selected python3 is older than Python 3.11.")
            remediation.append(
                "Put Python 3.11+ before older Python on the agent PATH; "
                "restart the shell and recheck hook interpreter selection."
            )
            checks["hook_python"]["ok"] = False
    settings: dict[str, Any] = {}
    config = home / ("settings.json" if agent == "claude" else "config.toml")
    try:
        if config.exists():
            settings = (
                json.loads(config.read_text())
                if agent == "claude"
                else tomllib.loads(config.read_text())
            )
        if not isinstance(settings, dict):
            raise ValueError("Configuration must be an object")  # noqa: TRY004
        checks["configuration"] = {"ran": True, "ok": True, "present": config.exists()}
    except (OSError, ValueError):
        checks["configuration"] = {"ran": True, "ok": False}
        remediation.append("Repair the selected account configuration syntax.")
        settings = {}
    enabled = settings.get("enabledPlugins", {})
    checks["plugin_overrides"] = (
        {key: val for key, val in enabled.items() if isinstance(val, bool)}
        if isinstance(enabled, dict)
        else {}
    )
    skills = (
        settings.get("permissions", {}).get("deny", [])
        if isinstance(settings.get("permissions", {}), dict)
        else []
    )
    checks["skill_overrides"] = [
        item
        for item in skills
        if isinstance(item, str) and re.fullmatch(r"Skill\([\w:.*@/-]+\)", item)
    ]
    overrides = settings.get("skillOverrides", {})
    checks["skill_registration_overrides"] = (
        {
            key: value
            for key, value in overrides.items()
            if isinstance(key, str)
            and isinstance(value, (bool, str))
            and (isinstance(value, bool) or value in {"off", "on"})
        }
        if isinstance(overrides, dict)
        else {}
    )
    skill_root = home / "skills"
    checks["skill_files"] = {
        "ran": True,
        "ok": True,
        "count": sum((child / "SKILL.md").is_file() for child in skill_root.iterdir())
        if skill_root.is_dir()
        else 0,
    }
    hook_checks = []
    hook_sources = [settings]
    hooks_file = home / "hooks.json"
    if hooks_file.is_file():
        try:
            hook_sources.append(json.loads(hooks_file.read_text()))
        except (OSError, ValueError):
            warnings.append("The account hooks.json cannot be parsed.")
    installed = home / "plugins" / "installed_plugins.json"
    if installed.is_file():
        try:
            registry = json.loads(installed.read_text()).get("plugins", {})
            for plugin_id, entries in registry.items():
                if isinstance(enabled, dict) and enabled.get(plugin_id) is False:
                    continue
                for entry in entries if isinstance(entries, list) else []:
                    root = entry.get("installPath")
                    if not isinstance(root, str):
                        continue
                    definition = Path(root) / "hooks" / "hooks.json"
                    if definition.is_file():
                        content = json.loads(definition.read_text())
                        content["_plugin_root"] = root
                        hook_sources.append(content)
        except (OSError, ValueError, AttributeError):
            warnings.append(
                "Installed plugin hook registrations could not be inspected."
            )
    for source in hook_sources:
        events = source.get("hooks", {}) if isinstance(source, dict) else {}
        if not isinstance(events, dict):
            warnings.append("Hook configuration has an unsupported structure.")
            continue
        for groups in events.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for hook in group.get("hooks", []):
                    if not isinstance(hook, dict):
                        continue
                    command = hook.get("command", "")
                    if not isinstance(command, str):
                        continue
                    try:
                        words = shlex.split(command)
                    except ValueError:
                        hook_checks.append(
                            {
                                "ran": True,
                                "ok": False,
                                "reason": "Cannot parse hook command",
                            }
                        )
                        continue
                    command = command.replace(
                        "${CLAUDE_PLUGIN_ROOT}", source.get("_plugin_root", "")
                    )
                    paths = re.findall(r"/[^\s\"'<>;]+\.(?:py|sh)\b", command)
                    missing_paths = sum(not Path(path).is_file() for path in paths)
                    launcher = words[0] if words else ""
                    found = bool(shutil.which(launcher)) if launcher else False
                    hook_checks.append(
                        {
                            "ran": True,
                            "ok": found and not missing_paths,
                            "launcher_available": found,
                            "script_paths_checked": len(paths),
                            "missing_script_paths": missing_paths,
                            "runtime_executed": False,
                        }
                    )
    checks["configured_hooks"] = hook_checks
    warnings.append(
        "Hook commands were inspected, not executed. Plugin hook imports "
        "and machine services require a separate safe runtime check."
    )
    checks["chrome_loopback"] = {"ran": True, "ok": False, "port": 9222}
    try:
        with socket.create_connection(("127.0.0.1", 9222), timeout=0.2):
            checks["chrome_loopback"]["ok"] = True
    except OSError:
        pass
    warnings.append(
        "A Chrome port listener does not prove a logged-in browser or MCP "
        "connection; browser authentication is machine-specific."
    )
    if not checks.get("native_plugins", {}).get("ok", False):
        remediation.append(
            f"Run {agent} plugin list --json with the selected account "
            "home; repair registrations without copying credentials."
        )
    return {
        "ran": True,
        "ok": True,
        "checks": checks,
        "warnings": warnings,
        "remediation": remediation,
    }


def inspect_environment(agent: str, home: Path) -> dict[str, Any]:
    """Inspect an account while containing native CLI writes in a private snapshot."""
    with tempfile.TemporaryDirectory(prefix="aichat-environment-") as directory:
        runtime_home = Path(directory)
        for relative in (
            "settings.json",
            "config.toml",
            "plugins/installed_plugins.json",
            "plugins/known_marketplaces.json",
        ):
            source = home / relative
            if source.is_file():
                target = runtime_home / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o600)
        result = _inspect_environment(agent, home, runtime_home)
        result["checks"]["native_probe_isolation"] = {
            "ran": True,
            "ok": True,
            "account_writes": "temporary snapshot",
            "cache_paths": "registrations may reference existing read-only inputs",
        }
        return result
