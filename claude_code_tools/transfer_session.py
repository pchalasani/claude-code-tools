"""Inspect and copy one agent conversation to a local or SSH destination."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import shlex
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

import click

from claude_code_tools.move_account import (
    SessionCandidate,
    _tiered_match,
    find_sessions_in_home,
)
from claude_code_tools.resolve_session_names import codex_thread_names
from claude_code_tools.transfer_remote import handle_request, project_state


def remote_bootstrap() -> str:
    """Build a standard-library helper; the destination need not install aichat."""
    modules = {}
    for name in ("transfer_codex", "transfer_remote"):
        path = Path(__file__).with_name(f"{name}.py")
        modules[f"claude_code_tools.{name}"] = path.read_text()
    encoded = base64.b64encode(json.dumps(modules).encode()).decode()
    return (
        "import sys,types,json,base64; "
        "assert sys.version_info >= (3,11), 'Python 3.11+ is required'; "
        "sys.modules['claude_code_tools']=types.ModuleType('claude_code_tools'); "
        f"sources=json.loads(base64.b64decode('{encoded}')); "
        "\nfor name,source in sources.items():\n"
        " module=types.ModuleType(name); sys.modules[name]=module; "
        "exec(compile(source,name,'exec'),module.__dict__)\n"
        "sys.modules['claude_code_tools.transfer_remote'].main()"
    )


def destination_request(
    host: str, python: str, request: dict[str, Any]
) -> dict[str, Any]:
    """Execute a destination operation without bypassing SSH host verification."""
    if host == "local":
        return handle_request(request)
    if host.startswith("-") or any(char.isspace() for char in host):
        raise ValueError("--to must be an SSH host/alias (or 'local'), not options")
    command = shlex.join([python, "-c", remote_bootstrap()])
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "--", host, command],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Destination helper failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout[:500]}"
        ) from error
    if result.returncode != 0 or response.get("ok") is not True:
        raise ValueError(response.get("error", result.stderr.strip()))
    return response


def prepare_transfer(
    agent: str,
    source_home: Path,
    session: str,
    destination_home: Path,
    destination_project: Path,
    staging: Path,
) -> dict[str, Any]:
    """Resolve a unique session and build a scoped, checksummed transfer plan."""
    if agent == "codex":
        database = source_home / "state_5.sqlite"
        names = codex_thread_names(source_home)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            candidates = [
                SessionCandidate(Path(path), sid, name or names.get(sid, ""))
                for sid, path, name in db.execute(
                    "SELECT id, rollout_path, name FROM threads"
                )
            ]
        matches = _tiered_match(candidates, session)
    else:
        matches = find_sessions_in_home(source_home, session)
    if len(matches) != 1:
        candidates = ", ".join(candidate.session_id for candidate in matches[:10])
        raise ValueError(
            f"Expected one {agent} session in {source_home}; found {len(matches)}. "
            f"Use a full UUID. Candidates: {candidates or '(none)'}"
        )
    adapter = importlib.import_module(f"claude_code_tools.transfer_{agent}")
    manifest = adapter.export_session(
        source_home,
        matches[0].session_id,
        destination_home,
        destination_project,
        staging,
    )
    if manifest.get("ok") is not True:
        raise ValueError("Session adapter did not report successful export")
    manifest.update(
        {
            "agent": agent,
            "session_id": matches[0].session_id,
            "source_home": str(source_home),
            "destination_home": str(destination_home),
            "destination_project": str(destination_project),
            "project_state": project_state(Path(manifest["source_project"])),
        }
    )
    artifacts = {}
    for relative in manifest["files"]:
        data = (staging / "files" / relative).read_bytes()
        artifacts[relative] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    manifest["artifacts"] = artifacts
    variable = "CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"
    command = ["env", f"{variable}={destination_home}", agent]
    if agent == "codex":
        command += ["--cd", str(destination_project), "resume", matches[0].session_id]
    else:
        command += ["--resume", matches[0].session_id]
    manifest["resume_command"] = (
        f"cd {shlex.quote(str(destination_project))} && {shlex.join(command)}"
    )
    manifest.setdefault("warnings", []).extend(
        [
            (
                "Exit the source agent before copying; do not continue both copies. "
                "Live processes and running shell tasks are not relocated."
            ),
            (
                "Git revision and cleanliness are checked. Ignored files, credentials, "
                "dependencies, plugins, shared settings, and external services must be "
                "prepared separately on the destination."
            ),
            "The destination agent is not started. Source files are retained.",
        ]
    )
    return manifest


@click.command("transfer")
@click.argument("session")
@click.option("--agent", type=click.Choice(["claude", "codex"]), required=True)
@click.option(
    "--source-home",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Source profile (defaults to the agent's configured home).",
)
@click.option("--to", "host", required=True, help="SSH host/alias, or 'local'.")
@click.option("--destination-home", required=True, help="Destination agent profile.")
@click.option("--destination-project", required=True, help="Prepared Git worktree.")
@click.option(
    "--remote-python",
    default="python3",
    show_default=True,
    help="Python 3.11+ executable on the SSH destination.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Inspect without importing destination account data.",
)
@click.option("--json", "as_json", is_flag=True, help="Print a structured report.")
@click.pass_context
def transfer(
    ctx: click.Context,
    session: str,
    agent: str,
    source_home: Path | None,
    host: str,
    destination_home: str,
    destination_project: str,
    remote_python: str,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Copy a saved conversation and supported artifacts, preserving the source.

    Exit the source agent first. Prepare a clean destination Git worktree at the
    same commit. Inspect --dry-run before copying. Existing differing artifacts
    are never overwritten; account credentials/settings are not transferred.
    Quote destination paths beginning with ~ so the destination expands them.
    """
    variable = "CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"
    configured = (ctx.obj or {}).get(f"{agent}_home")
    source_home = (
        Path(source_home or configured or os.environ.get(variable, f"~/.{agent}"))
        .expanduser()
        .resolve()
    )
    request = {
        "operation": "probe",
        "destination_home": destination_home,
        "destination_project": destination_project,
    }
    manifest: dict[str, Any] | None = None
    try:
        probe = destination_request(host, remote_python, request)
        with tempfile.TemporaryDirectory(prefix="aichat-transfer-") as directory:
            staging = Path(directory)
            manifest = prepare_transfer(
                agent,
                source_home,
                session,
                Path(probe["destination_home"]),
                Path(probe["project"]["path"]),
                staging,
            )
            request.update({"operation": "validate", "manifest": manifest})
            result = destination_request(host, remote_python, request)
            if not dry_run:
                request["operation"] = "import"
                request["data"] = {
                    relative: base64.b64encode(
                        (staging / "files" / relative).read_bytes()
                    ).decode()
                    for relative in manifest["files"]
                }
                result = destination_request(host, remote_python, request)
            report = {
                "ok": True,
                "dry_run": dry_run,
                "plan": manifest,
                "destination": result,
            }
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        report = {"ok": False, "error": str(error), "plan": manifest}
    if as_json:
        click.echo(json.dumps(report, indent=2))
    elif not report["ok"]:
        click.echo(f"Transfer cannot proceed: {report['error']}", err=True)
    else:
        assert manifest is not None
        click.echo("Transfer plan verified." if dry_run else "Transfer verified.")
        click.echo(f"{agent} {manifest['session_id']} → {host}:{destination_home}")
        for relative, metadata in manifest["artifacts"].items():
            click.echo(f"  {relative} ({metadata['size']} bytes)")
        for database in manifest.get("databases", []):
            for table in database["tables"]:
                click.echo(
                    f"  {database['name']}:{table['name']} "
                    f"({len(table['rows'])} records)"
                )
        for warning in manifest["warnings"]:
            click.echo(f"Note: {warning}")
        click.echo(f"Resume on destination:\n  {manifest['resume_command']}")
    if not report["ok"]:
        ctx.exit(1)
