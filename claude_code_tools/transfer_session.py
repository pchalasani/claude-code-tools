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
    import inspect
    import zlib

    from claude_code_tools.session_utils import encode_claude_project_path

    modules = {
        f"claude_code_tools.{path.stem}": path.read_text()
        for path in Path(__file__).parent.glob("transfer_*.py")
        if path.stem != "transfer_session"
    }
    modules["claude_code_tools.session_utils"] = inspect.getsource(
        encode_claude_project_path
    )
    encoded = base64.b64encode(zlib.compress(json.dumps(modules).encode())).decode()
    return f"""import sys, types, json, base64, zlib, importlib.abc, importlib.util
assert sys.version_info >= (3,11), 'Python 3.11+ is required'
sources = json.loads(zlib.decompress(base64.b64decode({encoded!r})))
package = types.ModuleType('claude_code_tools')
package.__path__ = []
sys.modules['claude_code_tools'] = package
class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in sources:
            return importlib.util.spec_from_loader(fullname, self)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        exec(compile(sources[module.__name__], module.__name__, 'exec'), module.__dict__)
sys.meta_path.insert(0, Loader())
from claude_code_tools.transfer_remote import main
main()
"""


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
    path_mappings: dict[str, str] | None = None,
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
        path_mappings=path_mappings,
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


def stage_path_guide(manifest: dict[str, Any], staging: Path) -> None:
    """Persist artifact mappings without rewriting historical conversation text."""
    relative = f"transfer-support/{manifest['session_id']}/path-map.json"
    guide = {
        "purpose": "Resolve historical artifact paths on this destination",
        "instructions": (
            "Use the longest matching source prefix in path_mappings. "
            "Conversation text is unchanged. missing_at_source entries were "
            "already absent before this copy and may need to be recreated."
        ),
        "path_mappings": manifest.get("path_mappings", {}),
        "missing_at_source": manifest.get("missing_at_source", []),
    }
    content = (json.dumps(guide, indent=2) + "\n").encode()
    target = staging / "files" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    manifest["files"].append(relative)
    manifest["artifacts"][relative] = {
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    manifest["path_guide"] = str(Path(manifest["destination_home"]) / relative)


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Summarize imported records without printing conversation or goal contents."""
    import copy

    result = copy.deepcopy(report)
    plan = result.get("plan") or {}
    for database in plan.get("databases", []):
        for table in database.get("tables", []):
            table["row_count"] = len(table.pop("rows", []))
    for update in plan.get("metadata_updates", []):
        update["row_count"] = len(update.pop("rows", []))
    return result


@click.command("transfer")
@click.argument("session")
@click.option("--agent", type=click.Choice(["claude", "codex"]), required=True)
@click.option(
    "--source-home",
    type=click.Path(file_okay=False, path_type=Path),
    help="Source profile (defaults to the agent's configured home).",
)
@click.option(
    "--from", "source_host", default="local", help="Source SSH alias, or local."
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
    "--map",
    "path_mappings",
    type=(str, str),
    multiple=True,
    help="Additional source/destination path pair; repeat as needed.",
)
@click.option(
    "--recover",
    is_flag=True,
    help="With --apply, retry the same interrupted transfer plan.",
)
@click.option("--apply", is_flag=True, help="Apply the verified copy plan.")
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
    source_host: str,
    host: str,
    destination_home: str,
    destination_project: str,
    remote_python: str,
    dry_run: bool,
    apply: bool,
    recover: bool,
    path_mappings: tuple[tuple[str, str], ...],
    as_json: bool,
) -> None:
    """Copy a saved conversation and supported artifacts, preserving the source.

    Exit the source agent first. Prepare a clean destination Git worktree at the
    same commit. Inspect the default dry-run plan, then use --apply to copy. Existing differing artifacts
    are never overwritten; account credentials/settings are not transferred.
    Quote destination paths beginning with ~ so the destination expands them.
    """
    if apply and dry_run:
        raise click.UsageError("Use either --apply or --dry-run, not both.")
    if recover and not apply:
        raise click.UsageError("--recover requires --apply")
    mapped: dict[str, str] = {}
    for old, new in path_mappings:
        if not Path(old).is_absolute() or not Path(new).is_absolute():
            raise click.UsageError("--map requires two absolute paths")
        if old in mapped and mapped[old] != new:
            raise click.UsageError("Conflicting destinations for the same --map source")
        mapped[old] = new
    dry_run = not apply
    variable = "CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"
    configured = (ctx.obj or {}).get(f"{agent}_home")
    if source_host == "local":
        source_home = (
            Path(source_home or configured or os.environ.get(variable, f"~/.{agent}"))
            .expanduser()
            .resolve()
        )
    else:
        source_home = Path(source_home or f"~/.{agent}")
    request = {
        "operation": "probe",
        "agent": agent,
        "destination_home": destination_home,
        "destination_project": destination_project,
    }
    manifest: dict[str, Any] | None = None
    try:
        probe = destination_request(host, remote_python, request)
        with tempfile.TemporaryDirectory(prefix="aichat-transfer-") as directory:
            staging = Path(directory)
            if source_host == "local":
                manifest = prepare_transfer(
                    agent,
                    source_home,
                    session,
                    Path(probe["destination_home"]),
                    Path(probe["project"]["path"]),
                    staging,
                    dict(path_mappings),
                )
            else:
                bundle = destination_request(
                    source_host,
                    remote_python,
                    {
                        "operation": "export",
                        "agent": agent,
                        "path_mappings": dict(path_mappings),
                        "source_home": str(source_home),
                        "session": session,
                        "destination_home": probe["destination_home"],
                        "destination_project": probe["project"]["path"],
                    },
                )
                manifest = bundle["manifest"]
                from claude_code_tools.transfer_remote import safe_target

                for relative, encoded in bundle["data"].items():
                    target = safe_target(staging / "files", relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(encoded, validate=True))
                command = ["env", f"{variable}={probe['destination_home']}", agent]
                if agent == "codex":
                    command += ["--cd", probe["project"]["path"], "resume"]
                else:
                    command += ["--resume"]
                command.append(manifest["session_id"])
                manifest["resume_command"] = (
                    f"cd {shlex.quote(probe['project']['path'])} && "
                    + shlex.join(command)
                )
            if source_host == "local":
                from claude_code_tools.transfer_environment import inspect_environment

                source_environment = inspect_environment(agent, source_home)
            else:
                source_environment = bundle.get("environment", {})
            stage_path_guide(manifest, staging)
            request.update({"operation": "validate", "manifest": manifest})
            result = destination_request(host, remote_python, request)
            if not dry_run:
                request["operation"] = "import"
                request["recover"] = recover
                request["data"] = {
                    relative: base64.b64encode(
                        (staging / "files" / relative).read_bytes()
                    ).decode()
                    for relative in manifest["files"]
                }
                result = destination_request(host, remote_python, request)
            from claude_code_tools.transfer_environment import compare_environments

            report = {
                "environment_comparison": compare_environments(
                    source_environment, probe.get("environment", {})
                ),
                "ran": True,
                "ok": True,
                "dry_run": dry_run,
                "plan": manifest,
                "destination": result,
                "environment": probe.get("environment"),
                "source_environment": source_environment,
            }
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        report = {"ran": True, "ok": False, "error": str(error), "plan": manifest}
    if as_json:
        click.echo(json.dumps(public_report(report), indent=2))
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
        for gap in manifest.get("missing_at_source", []):
            click.echo(f"Missing at source (not copied): {gap['path']}")
        for warning in manifest["warnings"]:
            click.echo(f"Note: {warning}")
        comparison = report.get("environment_comparison", {})
        for field in ("missing_or_disabled_plugins", "newly_disabled_skills"):
            if comparison.get(field):
                click.echo(f"Environment {field}: {', '.join(comparison[field])}")
        for remedy in comparison.get("remediation", []):
            click.echo(f"Remediation: {remedy}")
        environment = report.get("environment") or {}
        for warning in environment.get("warnings", []):
            click.echo(f"Environment: {warning}")
        for remedy in environment.get("remediation", []):
            click.echo(f"Remediation: {remedy}")
        click.echo(f"Artifact path guide: {manifest['path_guide']}")
        click.echo(f"Resume on destination:\n  {manifest['resume_command']}")
    if not report["ok"]:
        ctx.exit(1)
