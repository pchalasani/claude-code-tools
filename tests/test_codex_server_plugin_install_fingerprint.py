"""Fingerprint regressions for volatile remote-plugin installer files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import claude_code_tools.codex_server_fingerprint as fingerprinting
from claude_code_tools.codex_server import (
    _paths,
)
from claude_code_tools.codex_server_models import ServerPaths


def _plugin_artifact_snapshot(paths: ServerPaths) -> tuple[str, tuple[str, ...]]:
    """Return the standalone plugin-cache diagnostic snapshot."""
    return fingerprinting._plugin_artifact_snapshot(paths)


def test_remote_plugin_install_marker_rewrite_is_ignored(tmp_path: Path) -> None:
    """Installer marker rewrites do not roll an otherwise identical server."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugin = (
        paths.codex_home
        / "plugins/cache/openai-curated-remote/openai-templates"
    )
    plugin.mkdir(parents=True)
    plugin.joinpath("0.1.0/plugin.json").parent.mkdir()
    plugin.joinpath("0.1.0/plugin.json").write_text("{}", encoding="utf-8")
    marker = plugin / ".codex-remote-plugin-install.json"
    marker.write_text('{"schema_version": 1}', encoding="utf-8")
    before = _plugin_artifact_snapshot(paths)

    replacement = plugin / "marker.tmp"
    replacement.write_text('{"schema_version": 2}', encoding="utf-8")
    replacement.replace(marker)

    assert _plugin_artifact_snapshot(paths) == before


def test_remote_plugin_marker_can_change_during_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent marker rewrite does not trigger a bounded-retry failure."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugin = paths.codex_home / "plugins/cache/openai-curated-remote/sample"
    artifact = plugin / "0.1.0/plugin.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    marker = plugin / ".codex-remote-plugin-install.json"
    marker.write_text('{"schema_version": 1}', encoding="utf-8")
    original_hash = fingerprinting._hash_regular_file

    def replace_marker_then_hash(
        directory_fd: int,
        name: str,
        relative: Path,
        expected: os.stat_result,
        digest: fingerprinting.HashWriter,
        generation: fingerprinting.HashWriter,
        budget: list[int],
    ) -> None:
        replacement = plugin / "marker.tmp"
        replacement.write_text('{"schema_version": 2}', encoding="utf-8")
        replacement.replace(marker)
        original_hash(
            directory_fd,
            name,
            relative,
            expected,
            digest,
            generation,
            budget,
        )

    monkeypatch.setattr(
        fingerprinting,
        "_hash_regular_file",
        replace_marker_then_hash,
    )

    snapshot = _plugin_artifact_snapshot(paths)

    assert len(snapshot[0]) == 64


def test_remote_plugin_atomic_write_temp_file_is_ignored(tmp_path: Path) -> None:
    """The marker's short-lived tempfile is not a runtime plugin artifact."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugin = paths.codex_home / "plugins/cache/openai-curated-remote/sample"
    artifact = plugin / "0.1.0/plugin.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    before = _plugin_artifact_snapshot(paths)

    temporary = plugin / ".tmpwIcWm7"
    temporary.write_text("installer bookkeeping", encoding="utf-8")

    assert _plugin_artifact_snapshot(paths) == before


def test_remote_plugin_staging_directory_is_ignored(tmp_path: Path) -> None:
    """Concurrent remote-plugin staging activity is not a runtime input."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    staging = paths.codex_home / "plugins/.remote-plugin-install-staging"
    staging.mkdir(parents=True)
    before = _plugin_artifact_snapshot(paths)

    staging.joinpath("download.tmp").write_text("changing", encoding="utf-8")

    assert _plugin_artifact_snapshot(paths) == before


def test_staging_only_plugin_root_matches_missing_root(tmp_path: Path) -> None:
    """Creating only volatile staging does not change the plugin snapshot."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    before = _plugin_artifact_snapshot(paths)

    staging = paths.codex_home / "plugins/.remote-plugin-install-staging"
    staging.mkdir(parents=True)
    staging.joinpath("download.tmp").write_text("changing", encoding="utf-8")

    assert _plugin_artifact_snapshot(paths) == before


def test_remote_marketplace_catalog_cache_is_ignored(tmp_path: Path) -> None:
    """Asynchronous remote discovery refreshes do not roll the App Server."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    catalog = paths.codex_home / "cache/remote_plugin_catalog/catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text('{"fetched_at": "first"}', encoding="utf-8")
    before = _plugin_artifact_snapshot(paths)

    catalog.write_text('{"fetched_at": "second"}', encoding="utf-8")

    assert _plugin_artifact_snapshot(paths) == before


def test_same_content_plugin_rewrite_preserves_fingerprint(tmp_path: Path) -> None:
    """Installer metadata churn is separate from semantic plugin identity."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/cache/sample/plugin.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    before = _plugin_artifact_snapshot(paths)

    replacement = artifact.with_suffix(".replacement")
    replacement.write_text("{}", encoding="utf-8")
    replacement.replace(artifact)
    after = _plugin_artifact_snapshot(paths)

    assert after[0] == before[0]
    assert after[1] != before[1]


def test_remote_plugin_artifact_change_still_changes_fingerprint(
    tmp_path: Path,
) -> None:
    """Only installer metadata is ignored; installed plugin bytes remain inputs."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = (
        paths.codex_home
        / "plugins/cache/openai-curated-remote/sample/0.1.0/plugin.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"version": 1}', encoding="utf-8")
    before = _plugin_artifact_snapshot(paths)

    artifact.write_text('{"version": 2}', encoding="utf-8")

    assert _plugin_artifact_snapshot(paths)[0] != before[0]
