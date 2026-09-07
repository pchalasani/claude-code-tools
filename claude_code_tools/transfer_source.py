"""Resolve and export a session on a local or SSH source using only stdlib."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def resolve_session(home: Path, agent: str, query: str) -> str:
    """Resolve exact IDs, unique ID prefixes, or saved conversation names."""
    candidates: dict[str, set[str]] = {}
    if agent == "codex":
        with sqlite3.connect(
            (home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True
        ) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(threads)")}
            fields = [field for field in ("name", "title") if field in columns]
            for row in db.execute("SELECT id," + ",".join(fields) + " FROM threads"):
                candidates[row[0]] = {value for value in row[1:] if value}
        from claude_code_tools.transfer_codex_artifacts import latest_session_index

        for sid, row in latest_session_index(home).items():
            if sid in candidates and isinstance(row.get("thread_name"), str):
                candidates[sid].add(row["thread_name"])
    else:
        for path in (home / "projects").glob("*/*.jsonl"):
            names = candidates.setdefault(path.stem, set())
            if query == path.stem:
                return path.stem
            with path.open() as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        for key in ("customTitle", "custom_title", "name"):
                            if isinstance(record.get(key), str):
                                names.add(record[key])
    if query in candidates:
        return query
    for matches in (
        [sid for sid in candidates if sid.startswith(query)],
        [sid for sid, names in candidates.items() if query in names],
    ):
        if matches:
            if len(matches) == 1:
                return matches[0]
            raise ValueError("Ambiguous session; use a full UUID")
    raise ValueError("No matching session in the selected source account")


def export_request(request: dict[str, Any]) -> dict[str, Any]:
    """Export a checksummed bundle; never launch the source agent."""
    from claude_code_tools.transfer_remote import project_state

    agent = request["agent"]
    if agent not in {"claude", "codex"}:
        raise ValueError("Unsupported agent")
    home = Path(request["source_home"]).expanduser().resolve()
    sid = resolve_session(home, agent, request["session"])
    adapter = importlib.import_module(f"claude_code_tools.transfer_{agent}")
    with tempfile.TemporaryDirectory(prefix="aichat-source-") as directory:
        staging = Path(directory)
        manifest = adapter.export_session(
            home,
            sid,
            Path(request["destination_home"]),
            Path(request["destination_project"]),
            staging,
            path_mappings=request.get("path_mappings"),
        )
        if manifest.get("ok") is not True:
            raise ValueError("Source adapter did not confirm export")
        manifest.update(
            {
                "agent": agent,
                "session_id": sid,
                "source_home": str(home),
                "destination_home": request["destination_home"],
                "destination_project": request["destination_project"],
                "project_state": project_state(Path(manifest["source_project"])),
            }
        )
        artifacts = {}
        data = {}
        for relative in manifest["files"]:
            content = (staging / "files" / relative).read_bytes()
            artifacts[relative] = {
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            data[relative] = base64.b64encode(content).decode()
        manifest["artifacts"] = artifacts
        from claude_code_tools.transfer_environment import inspect_environment

        return {
            "ran": True,
            "ok": True,
            "manifest": manifest,
            "data": data,
            "environment": inspect_environment(agent, home),
        }
