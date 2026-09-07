"""Codex structural path translation and referenced supporting artifacts."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from pathlib import Path
from typing import Any


def latest_session_index(home: Path) -> dict[str, dict[str, Any]]:
    """Read the last appended index row per ID, as native rename history does."""
    path = home / "session_index.jsonl"
    if not path.is_file():
        return {}
    latest = {}
    with path.open() as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError("Malformed Codex session index row")  # noqa: TRY004
            latest[row["id"]] = row
    return latest


class CodexArtifacts:
    """Collect selected references without copying account configuration or auth."""

    def __init__(
        self,
        source_home: Path,
        destination_home: Path,
        source_project: Path,
        destination_project: Path,
        staging: Path,
        session_id: str,
        path_mappings: list[tuple[str, str]] | dict[str, str] | None,
    ) -> None:
        self.source_home = source_home
        self.destination_home = destination_home
        self.staging = staging
        self.session_id = session_id
        additional = (
            path_mappings.items()
            if isinstance(path_mappings, dict)
            else path_mappings or []
        )
        self.mappings = list(additional) + [
            (str(source_project), str(destination_project)),
            (str(source_home), str(destination_home)),
        ]
        self.historical: list[dict[str, str]] = []
        prior_map = source_home / "transfer-support" / session_id / "path-map.json"
        if prior_map.is_file():
            self.historical = json.loads(prior_map.read_text()).get("path_mappings", [])
            self.historical.sort(key=lambda item: len(item["source"]), reverse=True)
        self.mappings.sort(key=lambda pair: len(pair[0]), reverse=True)
        self.references: set[str] = set()
        self.files: list[str] = []
        self.missing: list[str] = []
        self.reference_mappings: list[dict[str, str]] = []

    def map_path(self, value: str) -> str:
        """Translate only an absolute path's longest explicitly mapped prefix."""
        for old, new in self.mappings:
            try:
                return str(Path(new) / Path(value).relative_to(old))
            except ValueError:
                continue
        return value

    def map_required(self, value: str) -> str:
        """Refuse operational paths outside all configured mappings."""
        normalized = posixpath.normpath(value)
        result = self.map_path(normalized)
        if result == normalized and not any(
            normalized == old or normalized.startswith(old + "/")
            for old, _ in self.mappings
        ):
            raise ValueError(f"Descendant working directory needs a mapping: {value}")
        return result

    def structural(self, value: Any) -> Any:
        """Translate path-valued structural metadata, leaving prose elsewhere alone."""
        if isinstance(value, str) and value.startswith("/"):
            return self.map_required(value)
        if isinstance(value, list):
            return [self.structural(item) for item in value]
        if isinstance(value, dict):
            return {key: self.structural(item) for key, item in value.items()}
        return value

    def discover(self, text: str) -> None:
        """Recognize literal support references in the selected session only."""
        for value in re.findall(r'/[^\s"\'<>`]+', text):
            value = value.rstrip(".,:;)\\")
            for mapping in self.historical:
                try:
                    tail = Path(value).relative_to(mapping["source"])
                    value = str(Path(mapping["destination"]) / tail)
                    break
                except ValueError:
                    continue
            path = Path(value)
            try:
                relative = path.relative_to(self.source_home)
            except ValueError:
                if value.startswith(("/tmp/", "/private/tmp/")):
                    self.references.add(value)
                continue
            if relative.parts and relative.parts[0] in {
                "attachments",
                "memories",
                "shell_snapshots",
                "tool-results",
                "scratch",
            }:
                self.references.add(value)

    def rollout(self, source: Path, target: Path) -> dict[int, int]:
        """Rewrite structural JSONL fields and return exact record-boundary offsets."""
        raw = source.read_bytes()
        output = bytearray()
        offsets = {}
        position = 0
        for line in raw.splitlines(keepends=True):
            offsets[position] = len(output)
            original_length = len(line)
            record = json.loads(line)
            self.discover(line.decode())
            before = json.dumps(record, sort_keys=True)
            if record.get("type") in ("session_meta", "turn_context"):
                payload = record.get("payload", {})
                for key in ("cwd", "sandbox_policy", "permissions"):
                    if key in payload:
                        payload[key] = self.structural(payload[key])
            if json.dumps(record, sort_keys=True) != before:
                line = (json.dumps(record, ensure_ascii=False) + "\n").encode()
            output.extend(line)
            position += original_length
        offsets[len(raw)] = len(output)
        target.write_bytes(output)
        return offsets

    def finish(self, ids: list[str]) -> dict[str, Any]:
        """Stage references and return shared-index updates for a guarded merge."""
        updates = []
        for name, key, container in (
            ("session_index.jsonl", "id", None),
            ("external_agent_session_imports.json", "imported_thread_id", "records"),
        ):
            path = self.source_home / name
            if not path.is_file():
                continue
            data = path.read_text()
            rows = (
                json.loads(data).get(container, [])
                if container
                else list(latest_session_index(self.source_home).values())
            )
            selected = [row for row in rows if row.get(key) in ids]
            if selected:
                updates.append(
                    {
                        "path": name,
                        "format": "json" if container else "jsonl",
                        "key": key,
                        "container": container,
                        "rows": selected,
                    }
                )
        for value in sorted(self.references):
            source = Path(value)
            if source.is_dir():
                continue
            if not source.is_file():
                self.missing.append(value)
                continue
            # Never follow a reference into configuration or an authentication file.
            resolved = source.resolve()
            if any(
                part
                in {
                    "auth.json",
                    "credentials.json",
                    ".credentials.json",
                    ".ssh",
                    ".aws",
                    ".config",
                }
                for part in resolved.parts
            ):
                raise ValueError(
                    "Referenced support artifact resolves to credentials/config"
                )
            try:
                relative = str(source.relative_to(self.source_home))
            except ValueError:
                digest = hashlib.sha256(value.encode()).hexdigest()[:16]
                relative = f"transfer-support/{self.session_id}/{digest}/{source.name}"
            target = self.staging / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            before = source.stat()
            data = source.read_bytes()
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("Supporting artifact changed during export")
            target.write_bytes(data)
            self.files.append(relative)
            self.reference_mappings.append(
                {
                    "source": value,
                    "destination": str(self.destination_home / relative),
                }
            )
        return {
            "files": self.files,
            "missing_at_source": self.missing,
            "path_mappings": [
                {"source": old, "destination": new} for old, new in self.mappings
            ]
            + self.reference_mappings
            + [
                {
                    "source": item["source"],
                    "destination": self.map_path(item["destination"]),
                }
                for item in self.historical
            ],
            "metadata_updates": updates,
        }
