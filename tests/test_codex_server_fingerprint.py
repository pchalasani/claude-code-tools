"""Focused regressions for bounded Codex plugin fingerprint inputs."""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import claude_code_tools.codex_server_fingerprint as fingerprinting
from claude_code_tools.codex_server import (
    CodexServerError,
    _paths,
    _plugin_configuration_snapshot,
)
from claude_code_tools.codex_server_models import (
    OwnedServer,
    StateFileError,
    read_state,
)


@pytest.mark.parametrize(
    ("relative", "is_file"),
    [
        (Path("config.toml"), True),
        (Path("plugins"), False),
        (Path("cache/remote_plugin_catalog"), False),
    ],
)
def test_semantically_missing_plugin_inputs_ignore_completed_aba(
    tmp_path: Path,
    relative: Path,
    is_file: bool,
) -> None:
    """A completed no-content ABA does not cause a false snapshot change."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    target = paths.codex_home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    before = _plugin_configuration_snapshot(paths)
    if is_file:
        target.write_text("", encoding="utf-8")
        target.unlink()
    else:
        target.mkdir()
        shutil.rmtree(target)

    after = _plugin_configuration_snapshot(paths)

    assert after == before


@pytest.mark.parametrize(
    "feature",
    [
        "apps",
        "enable_mcp_apps",
        "plugins",
        "plugin_sharing",
        "remote_plugin",
    ],
)
def test_plugin_feature_flags_participate_in_fingerprint(
    tmp_path: Path,
    feature: str,
) -> None:
    """Plugin feature gates are part of the persisted certification."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    config = paths.codex_home / "config.toml"
    config.write_text(f"[features]\n{feature} = false\n", encoding="utf-8")
    before = _plugin_configuration_snapshot(paths)
    config.write_text(f"[features]\n{feature} = true\n", encoding="utf-8")

    assert _plugin_configuration_snapshot(paths).fingerprint != before.fingerprint


def test_codex_marketplace_sync_metadata_does_not_roll_server(
    tmp_path: Path,
) -> None:
    """Codex-owned sync observations are not process-generation inputs."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    config = paths.codex_home / "config.toml"
    config.write_text(
        """
[marketplaces.example]
source_type = "git"
source = "https://example.com/plugins.git"
last_updated = "2026-08-14T12:00:00Z"
last_revision = "old"
""".lstrip(),
        encoding="utf-8",
    )
    before = _plugin_configuration_snapshot(paths)
    config.write_text(
        """
[marketplaces.example]
source_type = "git"
source = "https://example.com/plugins.git"
last_updated = "2026-08-14T17:10:14Z"
last_revision = "new"
""".lstrip(),
        encoding="utf-8",
    )

    after = _plugin_configuration_snapshot(paths)

    assert after.fingerprint == before.fingerprint
    assert after.generation != before.generation


def test_marketplace_source_change_rolls_server(tmp_path: Path) -> None:
    """A user-selected marketplace source remains a generation input."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    config = paths.codex_home / "config.toml"
    config.write_text(
        '[marketplaces.example]\nsource = "https://example.com/one.git"\n',
        encoding="utf-8",
    )
    before = _plugin_configuration_snapshot(paths)
    config.write_text(
        '[marketplaces.example]\nsource = "https://example.com/two.git"\n',
        encoding="utf-8",
    )

    assert _plugin_configuration_snapshot(paths).fingerprint != before.fingerprint


def test_runtime_plugin_cache_change_does_not_roll_server(tmp_path: Path) -> None:
    """The App Server's dynamically refreshed cache is not immutable state."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/cache/example/plugin.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"version": 1}', encoding="utf-8")
    before = _plugin_configuration_snapshot(paths)
    artifact.write_text('{"version": 2}', encoding="utf-8")

    assert _plugin_configuration_snapshot(paths) == before


def test_plugin_regular_file_content_is_hashed_when_metadata_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal metadata cannot hide a same-size plugin content rewrite."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/plugin.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("AAAA", encoding="utf-8")
    monkeypatch.setattr(
        fingerprinting,
        "_stat_generation",
        lambda _info: "fixed generation",
    )
    before = fingerprinting._plugin_artifact_snapshot(paths)
    artifact.write_text("BBBB", encoding="utf-8")

    assert fingerprinting._plugin_artifact_snapshot(paths)[0] != before[0]


def test_artifact_added_during_empty_root_certification_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real artifact cannot appear after the empty-root name scan."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugins = paths.codex_home / "plugins"
    plugins.mkdir(parents=True)
    original_check = fingerprinting._require_same_directory_path
    inserted = False

    def insert_before_path_check(
        path: Path,
        expected: os.stat_result,
        message: str,
    ) -> None:
        nonlocal inserted
        if path == plugins and not inserted:
            inserted = True
            plugins.joinpath("plugin.json").write_text("{}", encoding="utf-8")
        original_check(path, expected, message)

    monkeypatch.setattr(
        fingerprinting,
        "_require_same_directory_path",
        insert_before_path_check,
    )

    with pytest.raises(
        fingerprinting.PluginSnapshotChangedError,
        match="cache changed",
    ):
        fingerprinting._plugin_artifact_snapshot(paths)


def test_atomic_configuration_replacement_is_a_retryable_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open descriptor cannot hide replacement of its configuration path."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    config = paths.codex_home / "config.toml"
    replacement = paths.codex_home / "replacement.toml"
    config.write_text("[features]\nplugins = true\n", encoding="utf-8")
    replacement.write_text("[features]\nplugins = false\n", encoding="utf-8")
    original_read = fingerprinting._read_bounded

    def replace_after_read(fd: int, limit: int) -> bytes:
        data = original_read(fd, limit)
        replacement.replace(config)
        return data

    monkeypatch.setattr(fingerprinting, "_read_bounded", replace_after_read)

    with pytest.raises(
        fingerprinting.PluginSnapshotChangedError,
        match="configuration changed",
    ):
        _plugin_configuration_snapshot(paths)


@pytest.mark.parametrize("input_name", ["configuration", "plugin-root"])
@pytest.mark.parametrize("error_number", [errno.ENOTDIR, errno.ESTALE])
def test_initial_plugin_input_race_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_name: str,
    error_number: int,
) -> None:
    """Initial-open path races enter the bounded snapshot retry policy."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    target = (
        paths.codex_home / "config.toml"
        if input_name == "configuration"
        else paths.codex_home / "plugins"
    )
    original_open = os.open

    def racing_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if os.fspath(path) == os.fspath(target):
            raise OSError(error_number, os.strerror(error_number))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(fingerprinting.os, "open", racing_open)

    with pytest.raises(fingerprinting.PluginSnapshotChangedError):
        if input_name == "configuration":
            _plugin_configuration_snapshot(paths)
        else:
            fingerprinting._plugin_artifact_snapshot(paths)


@pytest.mark.parametrize(
    "error_number",
    [errno.ENOENT, errno.ENOTDIR, errno.ESTALE],
)
def test_configuration_descriptor_race_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    """Descriptor-phase configuration errors enter the bounded retry policy."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    paths.codex_home.joinpath("config.toml").write_text("", encoding="utf-8")

    def racing_read(_fd: int, _limit: int) -> bytes:
        raise OSError(error_number, os.strerror(error_number))

    monkeypatch.setattr(fingerprinting, "_read_bounded", racing_read)

    with pytest.raises(fingerprinting.PluginSnapshotChangedError):
        _plugin_configuration_snapshot(paths)


@pytest.mark.parametrize("input_name", ["configuration", "plugin-root"])
def test_missing_plugin_input_appearance_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_name: str,
) -> None:
    """A path that appears after missing-parent capture is not certified absent."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    target = (
        paths.codex_home / "config.toml"
        if input_name == "configuration"
        else paths.codex_home / "plugins"
    )
    original_missing = fingerprinting._missing_generation

    def appear_after_capture(path: Path) -> str:
        generation = original_missing(path)
        if path == target:
            if input_name == "configuration":
                path.write_text("", encoding="utf-8")
            else:
                path.mkdir()
        return generation

    monkeypatch.setattr(
        fingerprinting,
        "_missing_generation",
        appear_after_capture,
    )

    with pytest.raises(fingerprinting.PluginSnapshotChangedError):
        if input_name == "configuration":
            _plugin_configuration_snapshot(paths)
        else:
            fingerprinting._plugin_artifact_snapshot(paths)


@pytest.mark.parametrize("helper", ["absolute", "relative"])
@pytest.mark.parametrize("error_number", [errno.ENOTDIR, errno.ESTALE])
def test_missing_parent_race_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
    error_number: int,
) -> None:
    """Parent traversal races use the same retryable mutation signal."""
    root = tmp_path / "root"
    root.mkdir()
    original_open = os.open
    root_fd = original_open(root, fingerprinting._directory_flags())

    def racing_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        target = root / "missing" if helper == "absolute" else Path("missing")
        if os.fspath(path) == os.fspath(target):
            raise OSError(error_number, os.strerror(error_number))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(fingerprinting.os, "open", racing_open)
    try:
        with pytest.raises(fingerprinting.PluginSnapshotChangedError):
            if helper == "absolute":
                fingerprinting._missing_generation(root / "missing/child")
            else:
                fingerprinting._missing_generation_at(root_fd, "missing/child")
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("helper", ["absolute", "relative"])
def test_transient_missing_existing_parent_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    """One false ENOENT cannot omit an existing nearest parent generation."""
    root = tmp_path / "root"
    existing = root / "existing"
    existing.mkdir(parents=True)
    original_open = os.open
    root_fd = original_open(root, fingerprinting._directory_flags())
    injected = False

    def racing_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal injected
        target = existing if helper == "absolute" else Path("existing")
        if not injected and os.fspath(path) == os.fspath(target):
            injected = True
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(fingerprinting.os, "open", racing_open)
    try:
        with pytest.raises(fingerprinting.PluginSnapshotChangedError):
            if helper == "absolute":
                fingerprinting._missing_generation(existing / "child")
            else:
                fingerprinting._missing_generation_at(
                    root_fd,
                    "existing/child",
                )
    finally:
        os.close(root_fd)


def test_absolute_missing_parent_replacement_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replaced nearest parent cannot certify an absolute path as absent."""
    root = tmp_path / "root"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    root.mkdir()
    replacement.mkdir()
    original_require_missing = fingerprinting._require_missing_path

    def replace_parent(path: Path, message: str) -> None:
        original_require_missing(path, message)
        root.rename(displaced)
        replacement.rename(root)

    monkeypatch.setattr(
        fingerprinting,
        "_require_missing_path",
        replace_parent,
    )

    with pytest.raises(fingerprinting.PluginSnapshotChangedError):
        fingerprinting._missing_generation(root / "missing")


def test_relative_missing_parent_replacement_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replaced nearest parent cannot certify a relative target as absent."""
    root = tmp_path / "root"
    parent = root / "parent"
    replacement = root / "replacement"
    displaced = root / "displaced"
    parent.mkdir(parents=True)
    replacement.mkdir()
    root_fd = os.open(root, fingerprinting._directory_flags())
    original_require_missing = fingerprinting._require_missing_path_at

    def replace_parent(
        directory_fd: int,
        name: str,
        message: str,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        original_require_missing(
            directory_fd,
            name,
            message,
            follow_symlinks=follow_symlinks,
        )
        parent.rename(displaced)
        replacement.rename(parent)

    monkeypatch.setattr(
        fingerprinting,
        "_require_missing_path_at",
        replace_parent,
    )
    try:
        with pytest.raises(fingerprinting.PluginSnapshotChangedError):
            fingerprinting._missing_generation_at(root_fd, "parent/missing")
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("helper", ["absolute", "relative"])
def test_missing_parent_fstat_race_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    """Descriptor metadata races use the bounded snapshot retry signal."""
    root = tmp_path / "root"
    root.mkdir()
    root_fd = os.open(root, fingerprinting._directory_flags())

    def stale_fstat(_fd: int) -> os.stat_result:
        raise OSError(errno.ESTALE, os.strerror(errno.ESTALE))

    monkeypatch.setattr(fingerprinting.os, "fstat", stale_fstat)
    try:
        with pytest.raises(fingerprinting.PluginSnapshotChangedError):
            if helper == "absolute":
                fingerprinting._missing_generation(root / "missing")
            else:
                fingerprinting._missing_generation_at(root_fd, "missing")
    finally:
        os.close(root_fd)


def test_atomic_plugin_root_replacement_is_a_retryable_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A traversal cannot certify a plugin root replaced at its pathname."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugins = paths.codex_home / "plugins"
    replacement = paths.codex_home / "replacement"
    displaced = paths.codex_home / "displaced"
    plugins.mkdir(parents=True)
    replacement.mkdir()
    original_names = fingerprinting._plugin_directory_names
    replaced = False

    def replace_after_names(
        directory_fd: int,
        relative: Path,
        budget: list[int] | None,
    ) -> list[str]:
        nonlocal replaced
        names = original_names(
            directory_fd,
            relative,
            budget,
        )
        if relative == Path("plugins") and not replaced:
            replaced = True
            plugins.rename(displaced)
            replacement.rename(plugins)
        return names

    monkeypatch.setattr(
        fingerprinting,
        "_plugin_directory_names",
        replace_after_names,
    )

    with pytest.raises(
        fingerprinting.PluginSnapshotChangedError,
        match="cache root changed",
    ):
        fingerprinting._plugin_artifact_snapshot(paths)


def test_plugin_entry_removal_during_traversal_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installer rename or removal enters the bounded snapshot retry path."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/plugin.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("plugin", encoding="utf-8")
    original_hash = fingerprinting._hash_regular_file

    def remove_before_open(
        directory_fd: int,
        name: str,
        relative: Path,
        expected: os.stat_result,
        digest: fingerprinting.HashWriter,
        generation: fingerprinting.HashWriter,
        budget: list[int],
    ) -> None:
        artifact.unlink()
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
        remove_before_open,
    )

    with pytest.raises(
        fingerprinting.PluginSnapshotChangedError,
        match="cache changed",
    ):
        fingerprinting._plugin_artifact_snapshot(paths)


def test_plugin_replacement_after_hash_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final entry check catches replacement after content was hashed."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/plugin.txt"
    replacement = paths.codex_home / "replacement.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old", encoding="utf-8")
    replacement.write_text("new", encoding="utf-8")
    original_hash = fingerprinting._hash_regular_file

    def replace_after_hash(
        directory_fd: int,
        name: str,
        relative: Path,
        expected: os.stat_result,
        digest: fingerprinting.HashWriter,
        generation: fingerprinting.HashWriter,
        budget: list[int],
    ) -> None:
        original_hash(
            directory_fd,
            name,
            relative,
            expected,
            digest,
            generation,
            budget,
        )
        replacement.replace(artifact)

    monkeypatch.setattr(
        fingerprinting,
        "_hash_regular_file",
        replace_after_hash,
    )

    with pytest.raises(
        fingerprinting.PluginSnapshotChangedError,
        match="directory changed",
    ):
        fingerprinting._plugin_artifact_snapshot(paths)


def test_nested_artifact_change_after_child_hash_changes_next_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later diagnostic scan observes a nested post-traversal change."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/nested/plugin.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old", encoding="utf-8")
    original_hash = fingerprinting._hash_directory
    changed = False

    def change_after_child_hash(
        directory_fd: int,
        relative: Path,
        digest: fingerprinting.HashWriter,
        generation: fingerprinting.HashWriter,
        budget: list[int],
        depth: int,
    ) -> None:
        nonlocal changed
        original_hash(
            directory_fd,
            relative,
            digest,
            generation,
            budget,
            depth,
        )
        if relative == Path("plugins/nested") and not changed:
            changed = True
            artifact.write_text("new", encoding="utf-8")

    monkeypatch.setattr(
        fingerprinting,
        "_hash_directory",
        change_after_child_hash,
    )

    before = fingerprinting._plugin_artifact_snapshot(paths)
    after = fingerprinting._plugin_artifact_snapshot(paths)

    assert after != before


def test_atomic_symlink_target_replacement_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An external target replacement cannot leave a stale plugin digest."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    target = tmp_path / "target.txt"
    replacement = tmp_path / "replacement.txt"
    link = paths.codex_home / "plugins/plugin.txt"
    link.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    replacement.write_text("new", encoding="utf-8")
    link.symlink_to(target)
    original_hash = fingerprinting._hash_regular_descriptor
    replaced = False

    def replace_after_hash(
        fd: int,
        relative: Path,
        initial_info: os.stat_result,
        digest: fingerprinting.HashWriter,
        generation: fingerprinting.HashWriter,
        budget: list[int],
    ) -> None:
        nonlocal replaced
        original_hash(
            fd,
            relative,
            initial_info,
            digest,
            generation,
            budget,
        )
        if not replaced:
            replaced = True
            replacement.replace(target)

    monkeypatch.setattr(
        fingerprinting,
        "_hash_regular_descriptor",
        replace_after_hash,
    )

    with pytest.raises(
        fingerprinting.PluginSnapshotChangedError,
        match="symlink target changed",
    ):
        fingerprinting._plugin_artifact_snapshot(paths)


def test_wide_plugin_tree_respects_file_descriptor_limit(tmp_path: Path) -> None:
    """Sibling breadth does not determine open descriptor usage."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugins = paths.codex_home / "plugins"
    plugins.mkdir(parents=True)
    for index in range(80):
        plugins.joinpath(f"plugin-{index}").mkdir()
    program = textwrap.dedent(
        f"""
        import resource
        from claude_code_tools.codex_server import _paths
        from claude_code_tools.codex_server_fingerprint import (
            _plugin_artifact_snapshot,
        )

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(24, hard), hard))
        paths = _paths({{"CODEX_HOME": {str(paths.codex_home)!r}}})
        snapshot = _plugin_artifact_snapshot(paths)
        assert len(snapshot[0]) == 64
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        text=True,
        timeout=5.0,
    )

    assert result.returncode == 0, result.stderr


def test_plugin_tree_aggregate_size_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plugin content hashing refuses work beyond its aggregate byte bound."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/plugin.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    monkeypatch.setattr(fingerprinting, "PLUGIN_TREE_MAX_BYTES", 3)

    with pytest.raises(CodexServerError, match="safe content size limit"):
        fingerprinting._plugin_artifact_snapshot(paths)


def test_normal_large_codex_plugin_binary_is_streamed(tmp_path: Path) -> None:
    """A regular plugin binary above 64 MiB remains a valid snapshot input."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    artifact = paths.codex_home / "plugins/.plugin-appserver/codex"
    artifact.parent.mkdir(parents=True)
    with artifact.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)

    snapshot = fingerprinting._plugin_artifact_snapshot(paths)

    assert len(snapshot[0]) == 64


def test_symlink_target_content_participates_in_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-path target rewrites invalidate a plugin symlink snapshot."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    target = tmp_path / "outside-skill.md"
    target.write_text("AAAA", encoding="utf-8")
    link = paths.codex_home / "plugins/plugin/skill.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(
        fingerprinting,
        "_stat_generation",
        lambda _info: "fixed generation",
    )
    before = fingerprinting._plugin_artifact_snapshot(paths)
    target.write_text("BBBB", encoding="utf-8")

    assert fingerprinting._plugin_artifact_snapshot(paths)[0] != before[0]


def test_dangling_plugin_symlink_detects_target_parent_aba(
    tmp_path: Path,
) -> None:
    """A missing followed target retains its target-parent generation."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    target_parent = tmp_path / "outside"
    target = target_parent / "skill.md"
    link = paths.codex_home / "plugins/plugin/skill.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    target_parent.mkdir()
    before = fingerprinting._plugin_artifact_snapshot(paths)
    target.write_text("temporary", encoding="utf-8")
    target.unlink()

    after = fingerprinting._plugin_artifact_snapshot(paths)

    assert after[0] == before[0]
    assert after[1] != before[1]


@pytest.mark.parametrize(
    "options",
    [
        ["--profile", "callbacks"],
        ["--profile=callbacks"],
        ["-p", "callbacks"],
    ],
)
def test_selected_profile_configuration_participates_in_fingerprint(
    tmp_path: Path,
    options: list[str],
) -> None:
    """The selected profile file is part of the server certification."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    profile = paths.codex_home / "callbacks.config.toml"
    profile.write_text("[plugins.sample]\nenabled = false\n", encoding="utf-8")
    before = _plugin_configuration_snapshot(paths, options)
    profile.write_text("[plugins.sample]\nenabled = true\n", encoding="utf-8")

    assert _plugin_configuration_snapshot(paths, options).fingerprint != (
        before.fingerprint
    )


def test_server_cli_options_participate_in_fingerprint(tmp_path: Path) -> None:
    """Plugin-affecting CLI overrides certify distinct server launches."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)

    enabled = _plugin_configuration_snapshot(paths, ["--enable", "plugins"])
    disabled = _plugin_configuration_snapshot(paths, ["--disable", "plugins"])

    assert enabled.fingerprint != disabled.fingerprint


def test_plugin_configuration_node_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broad configuration is bounded independently of its byte size."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    paths.codex_home.joinpath("config.toml").write_text(
        'plugins = ["one", "two", "three"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(fingerprinting, "PLUGIN_CONFIG_MAX_NODES", 3)

    with pytest.raises(CodexServerError, match="too many values"):
        _plugin_configuration_snapshot(paths)


def test_ownership_state_policy_rejects_ignored_deep_structure(
    tmp_path: Path,
) -> None:
    """A valid ownership envelope cannot smuggle unbounded ignored values."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.runtime_dir.mkdir(parents=True)
    state = OwnedServer(
        pid=12_345,
        pgid=12_345,
        process_started_at="identity",
        codex_path="/codex",
        codex_version="codex-cli 9.9.9",
        launched_at="now",
        phase="running",
    ).as_json()
    nested: object = 0
    for _index in range(40):
        nested = [nested]
    state["ignored"] = nested
    paths.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(StateFileError, match="nested too deeply"):
        read_state(paths)


@pytest.mark.parametrize("field", ["pid", "pgid", "workerPid", "workerPgid"])
def test_ownership_state_rejects_unbounded_process_identifiers(
    field: str,
) -> None:
    """Process identifiers must fit the OS APIs that consume them."""
    state = OwnedServer(
        pid=12_345,
        pgid=12_345,
        process_started_at="identity",
        codex_path="/codex",
        codex_version="codex-cli 9.9.9",
        launched_at="now",
        phase="running",
        worker_pid=12_346,
        worker_pgid=12_346,
        worker_started_at="worker identity",
    ).as_json()
    state[field] = 10**100

    with pytest.raises(StateFileError, match="invalid"):
        OwnedServer.from_json(state)


@pytest.mark.parametrize("input_name", ["config", "state"])
def test_config_and_state_parsing_fail_closed_without_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_name: str,
) -> None:
    """Parsing never falls back to following pathnames without OS support."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    monkeypatch.delattr(os, "O_NOFOLLOW")
    if input_name == "config":
        paths.codex_home.joinpath("config.toml").write_text("", encoding="utf-8")
        with pytest.raises(CodexServerError, match="O_NOFOLLOW"):
            _plugin_configuration_snapshot(paths)
    else:
        paths.runtime_dir.mkdir()
        paths.state_path.write_text("{}", encoding="utf-8")
        with pytest.raises(StateFileError, match="O_NOFOLLOW"):
            read_state(paths)
