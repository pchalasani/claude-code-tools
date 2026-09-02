"""Safe snapshots of Codex plugin configuration and cache inputs."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from claude_code_tools.codex_server_models import CodexServerError, ServerPaths
from claude_code_tools.codex_server_retry import PluginSnapshotChangedError


PLUGIN_CONFIG_MAX_BYTES = 1024 * 1024
PLUGIN_CONFIG_MAX_DEPTH = 64
PLUGIN_CONFIG_MAX_NODES = 16_384
PLUGIN_TREE_MAX_DEPTH = 64
PLUGIN_TREE_MAX_ENTRIES = 100_000
PLUGIN_TREE_MAX_BYTES = 512 * 1024 * 1024
PLUGIN_APP_FEATURES = (
    "apps",
    "enable_mcp_apps",
    "plugins",
    "plugin_sharing",
    "remote_plugin",
)
MARKETPLACE_RUNTIME_FIELDS = {
    "last_revision",
    "last_updated",
}
_PLUGIN_MUTATION_ERRNOS = {
    errno.ELOOP,
    errno.ENOENT,
    errno.ENOTDIR,
    errno.ESTALE,
}
_PLUGIN_PATH_RACE_ERRNOS = {
    errno.ENOTDIR,
    errno.ESTALE,
}
_REMOTE_INSTALL_TEMP_FILE = re.compile(r"^\.tmp[A-Za-z0-9]{6}$")


@dataclass(frozen=True)
class PluginSnapshot:
    """Plugin digest plus an input generation used for race detection."""

    fingerprint: str
    generation: str


class HashWriter(Protocol):
    """Minimal interface shared by hashlib digest implementations."""

    def update(self, data: bytes, /) -> None:
        """Add bytes to the digest."""


def plugin_configuration_snapshot(
    paths: ServerPaths,
    codex_options: Sequence[str] = (),
) -> PluginSnapshot:
    """Safely snapshot process-level plugin configuration."""
    config_path = paths.codex_home / "config.toml"
    config, config_generation = read_plugin_configuration(config_path)
    profile_name = _selected_profile(codex_options)
    profile: dict[str, object] = {}
    configuration_generations = [config_generation]
    if profile_name is not None:
        profile_path = paths.codex_home / f"{profile_name}.config.toml"
        profile, profile_generation = read_plugin_configuration(profile_path)
        configuration_generations.append(profile_generation)
    relevant = {
        "base": _relevant_configuration(config),
        "profile": _relevant_configuration(profile),
        "serverOptions": list(codex_options),
    }
    configuration = json.dumps(
        relevant,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(configuration).hexdigest()
    generation = hashlib.sha256(
        "\0".join(
            [
                *configuration_generations,
                fingerprint,
            ]
        ).encode()
    ).hexdigest()
    return PluginSnapshot(fingerprint=fingerprint, generation=generation)


def _plugin_artifact_snapshot(paths: ServerPaths) -> tuple[str, tuple[str, ...]]:
    """Hash installed plugin artifacts in one bounded verification pass."""
    artifacts = hashlib.sha256()
    relative = Path("plugins")
    generation = hash_plugin_tree(
        paths.codex_home / relative,
        relative,
        artifacts,
    )
    return artifacts.hexdigest(), (generation,)


def plugin_configuration_fingerprint(
    paths: ServerPaths,
    codex_options: Sequence[str] = (),
) -> str:
    """Return the persisted digest for the current plugin snapshot."""
    return plugin_configuration_snapshot(paths, codex_options).fingerprint


def _selected_profile(codex_options: Sequence[str]) -> str | None:
    """Return the last selected bounded profile name, if any."""
    selected: str | None = None
    index = 0
    while index < len(codex_options):
        option = codex_options[index]
        if option in {"--profile", "-p"}:
            index += 1
            if index < len(codex_options):
                selected = codex_options[index]
        elif option.startswith("--profile=") or option.startswith("-p="):
            selected = option.split("=", 1)[1]
        index += 1
    if selected is None:
        return None
    if not selected or Path(selected).name != selected:
        raise CodexServerError(f"invalid Codex profile name: {selected!r}")
    return selected


def _relevant_configuration(config: dict[str, object]) -> dict[str, object]:
    """Select plugin, marketplace, and app configuration from one layer."""
    features = config.get("features", {})
    if isinstance(features, dict):
        features = {
            key: features[key] for key in PLUGIN_APP_FEATURES if key in features
        }
    marketplaces = config.get("marketplaces", {})
    if isinstance(marketplaces, dict):
        marketplaces = {
            name: _stable_marketplace_configuration(value)
            for name, value in marketplaces.items()
        }
    return {
        "features": features,
        "marketplaces": marketplaces,
        "plugins": config.get("plugins", {}),
    }


def _stable_marketplace_configuration(value: object) -> object:
    """Remove Codex-maintained sync observations from one marketplace."""
    if not isinstance(value, dict):
        return value
    return {
        key: item
        for key, item in value.items()
        if key not in MARKETPLACE_RUNTIME_FIELDS
    }


def read_plugin_configuration(path: Path) -> tuple[dict[str, object], str]:
    """Read bounded TOML from a nonblocking, no-follow regular descriptor."""
    flags = os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag()
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        _missing_generation(path)
        _require_missing_path(
            path,
            f"Codex plugin configuration changed while being read: {path}",
        )
        return {}, f"missing-configuration:{path.name}"
    except OSError as exc:
        if exc.errno in _PLUGIN_PATH_RACE_ERRNOS:
            raise PluginSnapshotChangedError(
                f"Codex plugin configuration changed while being read: {path}"
            ) from exc
        raise CodexServerError(
            f"cannot read Codex plugin configuration {path}: {exc}"
        ) from exc
    try:
        initial_info = os.fstat(fd)
        if not stat.S_ISREG(initial_info.st_mode):
            raise CodexServerError(
                f"Codex plugin configuration must be a regular file: {path}"
            )
        data = _read_bounded(fd, PLUGIN_CONFIG_MAX_BYTES)
        generation = _stat_generation(os.fstat(fd))
        if generation != _stat_generation(initial_info):
            raise PluginSnapshotChangedError(
                f"Codex plugin configuration changed while being read: {path}"
            )
        _require_unchanged_path(
            path,
            initial_info,
            f"Codex plugin configuration changed while being read: {path}",
        )
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(
                f"Codex plugin configuration changed while being read: {path}"
            ) from exc
        raise CodexServerError(
            f"cannot read Codex plugin configuration {path}: {exc}"
        ) from exc
    finally:
        os.close(fd)
    try:
        config = tomllib.loads(data.decode("utf-8"))
        _require_bounded_configuration(config)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CodexServerError(
            f"cannot parse Codex plugin configuration {path}: {exc}"
        ) from exc
    return config, generation


def _require_bounded_configuration(value: object) -> None:
    """Reject configuration structures whose nesting is unsafe to serialize."""
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > PLUGIN_CONFIG_MAX_DEPTH:
            raise CodexServerError("Codex plugin configuration is nested too deeply")
        if nodes > PLUGIN_CONFIG_MAX_NODES:
            raise CodexServerError("Codex plugin configuration has too many values")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def hash_plugin_tree(root: Path, label: Path, digest: HashWriter) -> str:
    """Hash a bounded tree with depth-bounded no-follow descriptors."""
    flags = _directory_flags()
    try:
        root_fd = os.open(root, flags)
    except FileNotFoundError:
        digest.update(b"missing:" + os.fsencode(str(label)) + b"\0")
        _missing_generation(root)
        _require_missing_path(
            root,
            f"Codex plugin cache root changed while being read: {root!r}",
        )
        return _empty_tree_generation(label)
    except OSError as exc:
        if exc.errno in _PLUGIN_PATH_RACE_ERRNOS:
            raise PluginSnapshotChangedError(
                f"Codex plugin cache root changed while being read: {root!r}"
            ) from exc
        raise CodexServerError(
            f"cannot inspect Codex plugin cache {root!r}: {exc}"
        ) from exc
    generation = hashlib.sha256()
    budget = [0, 0]
    try:
        initial_info = os.fstat(root_fd)
        initial_names = _plugin_directory_names(root_fd, label, None)
        if initial_names:
            _hash_directory(root_fd, label, digest, generation, budget, 0)
            result = generation.hexdigest()
        else:
            digest.update(b"missing:" + os.fsencode(str(label)) + b"\0")
            if _plugin_directory_names(root_fd, label, None):
                raise PluginSnapshotChangedError(
                    f"Codex plugin cache changed while being read: {root!r}"
                )
            result = _empty_tree_generation(label)
        _require_same_directory_path(
            root,
            initial_info,
            f"Codex plugin cache root changed while being read: {root!r}",
        )
        if not initial_names:
            if _plugin_directory_names(root_fd, label, None):
                raise PluginSnapshotChangedError(
                    f"Codex plugin cache changed while being read: {root!r}"
                )
            _require_same_directory_path(
                root,
                initial_info,
                f"Codex plugin cache root changed while being read: {root!r}",
            )
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(
                f"Codex plugin cache changed while being read: {root!r}"
            ) from exc
        raise CodexServerError(
            f"cannot inspect Codex plugin cache {root!r}: {exc}"
        ) from exc
    finally:
        os.close(root_fd)
    return result


def _empty_tree_generation(label: Path) -> str:
    """Identify a plugin root with no runtime-relevant entries."""
    return f"empty-or-missing:{label}"


def _hash_directory(
    directory_fd: int,
    relative: Path,
    digest: HashWriter,
    generation: HashWriter,
    budget: list[int],
    depth: int,
) -> None:
    """Hash one directory before opening one child descriptor at a time."""
    if depth > PLUGIN_TREE_MAX_DEPTH:
        raise CodexServerError("Codex plugin cache is nested too deeply")
    initial_info = os.fstat(directory_fd)
    if not stat.S_ISDIR(initial_info.st_mode):
        raise CodexServerError(
            f"Codex plugin cache path is not a directory: {relative!r}"
        )
    _update_directory(digest, generation, relative, initial_info)
    names = _plugin_directory_names(directory_fd, relative, budget)
    observed_entries: dict[str, tuple[int, ...]] = {}
    for name in names:
        child_relative = relative / name
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        observed_entries[name] = _plugin_entry_identity(info)
        if stat.S_ISLNK(info.st_mode):
            _update_entry(digest, generation, b"symlink", child_relative, info)
            target = os.readlink(name, dir_fd=directory_fd)
            digest.update(os.fsencode(target) + b"\0")
            _hash_symlink_target(
                directory_fd,
                name,
                target,
                child_relative,
                digest,
                generation,
                budget,
                depth,
            )
        elif stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
            try:
                if _directory_identity(os.fstat(child_fd)) != _directory_identity(
                    info
                ):
                    raise PluginSnapshotChangedError(
                        "Codex plugin directory changed while being read: "
                        f"{child_relative!r}"
                    )
                _hash_directory(
                    child_fd,
                    child_relative,
                    digest,
                    generation,
                    budget,
                    depth + 1,
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            _hash_regular_file(
                directory_fd,
                name,
                child_relative,
                info,
                digest,
                generation,
                budget,
            )
        else:
            _update_entry(digest, generation, b"special", child_relative, info)
    final_info = os.fstat(directory_fd)
    final_names = _plugin_directory_names(directory_fd, relative, None)
    if (
        _directory_identity(final_info) != _directory_identity(initial_info)
        or final_names != names
        or any(
            _plugin_entry_identity(
                os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            )
            != observed_entries[name]
            for name in names
        )
    ):
        raise PluginSnapshotChangedError(
            f"Codex plugin directory changed while being read: {relative!r}"
        )


def _plugin_directory_names(
    directory_fd: int,
    relative: Path,
    budget: list[int] | None,
) -> list[str]:
    """Return sorted fingerprint-relevant children of one directory."""
    with os.scandir(directory_fd) as stream:
        names: list[str] = []
        for child in stream:
            if _ignored_plugin_entry(relative, child.name):
                continue
            if budget is not None:
                budget[0] += 1
                if budget[0] > PLUGIN_TREE_MAX_ENTRIES:
                    raise CodexServerError(
                        "Codex plugin cache contains too many entries to "
                        "snapshot safely"
                    )
            names.append(child.name)
    names.sort(key=os.fsencode)
    return names


def _ignored_plugin_entry(relative: Path, name: str) -> bool:
    """Exclude volatile installer bookkeeping that App Server does not load."""
    parts = relative.parts
    if parts == ("plugins",) and name == ".remote-plugin-install-staging":
        return True
    remote_plugin_root = (
        len(parts) == 4
        and parts[:3] == ("plugins", "cache", "openai-curated-remote")
    )
    return remote_plugin_root and (
        name == ".codex-remote-plugin-install.json"
        or _REMOTE_INSTALL_TEMP_FILE.fullmatch(name) is not None
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    """Return stable identity fields unaffected by child bookkeeping writes."""
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _plugin_entry_identity(info: os.stat_result) -> tuple[int, ...]:
    """Return stable directory identity or full mutable-entry generation."""
    if stat.S_ISDIR(info.st_mode):
        return _directory_identity(info)
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _update_directory(
    digest: HashWriter,
    generation: HashWriter,
    relative: Path,
    info: os.stat_result,
) -> None:
    """Hash a directory without volatile child-change timestamps."""
    identity = ":".join(str(item) for item in _directory_identity(info)).encode()
    record = b"directory:" + os.fsencode(str(relative)) + b"\0"
    digest.update(record)
    generation.update(record + identity + b"\0")


def _hash_regular_file(
    directory_fd: int,
    name: str,
    relative: Path,
    expected: os.stat_result,
    digest: HashWriter,
    generation: HashWriter,
    budget: list[int],
) -> None:
    """Hash one aggregate-bounded regular file through a verified descriptor."""
    flags = os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag()
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        initial_info = os.fstat(fd)
        if not stat.S_ISREG(initial_info.st_mode):
            raise PluginSnapshotChangedError(
                f"Codex plugin artifact changed while being read: {relative!r}"
            )
        if _stat_generation(initial_info) != _stat_generation(expected):
            raise PluginSnapshotChangedError(
                f"Codex plugin artifact changed while being read: {relative!r}"
            )
        _hash_regular_descriptor(
            fd,
            relative,
            initial_info,
            digest,
            generation,
            budget,
        )
    finally:
        os.close(fd)


def _hash_symlink_target(
    directory_fd: int,
    name: str,
    target: str,
    relative: Path,
    digest: HashWriter,
    generation: HashWriter,
    budget: list[int],
    depth: int,
) -> None:
    """Hash the content Codex observes after following a plugin symlink."""
    try:
        expected = os.stat(name, dir_fd=directory_fd, follow_symlinks=True)
    except FileNotFoundError:
        digest.update(b"dangling-symlink-target\0")
        target_generation = _missing_generation_at(directory_fd, target)
        generation.update(
            b"dangling-symlink-target:"
            + os.fsencode(target)
            + b"\0"
            + target_generation.encode()
            + b"\0"
        )
        _require_missing_path_at(
            directory_fd,
            name,
            f"Codex plugin symlink target changed: {relative!r}",
        )
        return
    if stat.S_ISDIR(expected.st_mode):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            if _stat_generation(os.fstat(fd)) != _stat_generation(expected):
                raise PluginSnapshotChangedError(
                    f"Codex plugin symlink target changed: {relative!r}"
                )
            _hash_directory(
                fd,
                relative / "<symlink-target>",
                digest,
                generation,
                budget,
                depth + 1,
            )
            _require_unchanged_path_at(
                directory_fd,
                name,
                expected,
                f"Codex plugin symlink target changed: {relative!r}",
            )
        finally:
            os.close(fd)
        return
    if not stat.S_ISREG(expected.st_mode):
        _update_entry(
            digest,
            generation,
            b"symlink-target-special",
            relative,
            expected,
        )
        _require_unchanged_path_at(
            directory_fd,
            name,
            expected,
            f"Codex plugin symlink target changed: {relative!r}",
        )
        return
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        initial_info = os.fstat(fd)
        if _stat_generation(initial_info) != _stat_generation(expected):
            raise PluginSnapshotChangedError(
                f"Codex plugin symlink target changed: {relative!r}"
            )
        _hash_regular_descriptor(
            fd,
            relative / "<symlink-target>",
            initial_info,
            digest,
            generation,
            budget,
        )
        _require_unchanged_path_at(
            directory_fd,
            name,
            expected,
            f"Codex plugin symlink target changed: {relative!r}",
        )
    finally:
        os.close(fd)


def _hash_regular_descriptor(
    fd: int,
    relative: Path,
    initial_info: os.stat_result,
    digest: HashWriter,
    generation: HashWriter,
    budget: list[int],
) -> None:
    """Stream one regular descriptor into the aggregate-bounded digest."""
    budget[1] += initial_info.st_size
    if budget[1] > PLUGIN_TREE_MAX_BYTES:
        raise CodexServerError("Codex plugin cache exceeds the safe content size limit")
    _update_entry(digest, generation, b"file", relative, initial_info)
    remaining = initial_info.st_size
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            raise PluginSnapshotChangedError(
                f"Codex plugin artifact changed while being read: {relative!r}"
            )
        digest.update(chunk)
        remaining -= len(chunk)
    digest.update(b"\0")
    if os.read(fd, 1):
        raise PluginSnapshotChangedError(
            f"Codex plugin artifact changed while being read: {relative!r}"
        )
    if _stat_generation(os.fstat(fd)) != _stat_generation(initial_info):
        raise PluginSnapshotChangedError(
            f"Codex plugin artifact changed while being read: {relative!r}"
        )


def _update_entry(
    digest: HashWriter,
    generation: HashWriter,
    kind: bytes,
    relative: Path,
    info: os.stat_result,
) -> None:
    """Hash semantic identity separately from race-detection metadata."""
    record = kind + b":" + os.fsencode(str(relative)) + b"\0"
    digest.update(record)
    generation.update(record + _stat_generation(info).encode() + b"\0")


def _require_unchanged_path(
    path: Path,
    expected: os.stat_result,
    message: str,
) -> None:
    """Reject atomic pathname replacement hidden by an open descriptor."""
    try:
        current = path.lstat()
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(message) from exc
        raise
    if _stat_generation(current) != _stat_generation(expected):
        raise PluginSnapshotChangedError(message)


def _require_same_directory_path(
    path: Path,
    expected: os.stat_result,
    message: str,
) -> None:
    """Reject directory replacement while tolerating ignored-child churn."""
    try:
        current = path.lstat()
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(message) from exc
        raise
    if _directory_identity(current) != _directory_identity(expected):
        raise PluginSnapshotChangedError(message)


def _require_missing_path(path: Path, message: str) -> None:
    """Require an input path to remain absent after parent-generation capture."""
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(message) from exc
        raise
    raise PluginSnapshotChangedError(message)


def _require_unchanged_path_at(
    directory_fd: int,
    name: str,
    expected: os.stat_result,
    message: str,
    *,
    follow_symlinks: bool = True,
) -> None:
    """Reject replacement of a followed path relative to a pinned directory."""
    try:
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=follow_symlinks,
        )
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(message) from exc
        raise
    if _stat_generation(current) != _stat_generation(expected):
        raise PluginSnapshotChangedError(message)


def _require_missing_path_at(
    directory_fd: int,
    name: str,
    message: str,
    *,
    follow_symlinks: bool = True,
) -> None:
    """Require a path to remain absent after missing-parent capture."""
    try:
        os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=follow_symlinks,
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno in _PLUGIN_MUTATION_ERRNOS:
            raise PluginSnapshotChangedError(message) from exc
        raise
    raise PluginSnapshotChangedError(message)


def _missing_generation(path: Path) -> str:
    """Identify a missing input through its nearest existing parent."""
    candidate = path.parent
    unresolved = [path.name]
    flags = _directory_flags()
    while True:
        try:
            fd = os.open(candidate, flags)
        except FileNotFoundError:
            if candidate == candidate.parent:
                raise CodexServerError(
                    f"cannot identify missing Codex plugin input {path}"
                ) from None
            unresolved.append(candidate.name)
            candidate = candidate.parent
            continue
        except OSError as exc:
            if exc.errno in _PLUGIN_PATH_RACE_ERRNOS:
                raise PluginSnapshotChangedError(
                    f"Codex plugin input changed while being read: {path}"
                ) from exc
            raise CodexServerError(
                f"cannot inspect parent of Codex plugin input {path}: {exc}"
            ) from exc
        message = f"Codex plugin input changed while being read: {path}"
        try:
            try:
                info = os.fstat(fd)
                _require_missing_path(candidate / unresolved[-1], message)
                _require_missing_path(path, message)
                _require_unchanged_path(candidate, info, message)
                if _stat_generation(os.fstat(fd)) != _stat_generation(info):
                    raise PluginSnapshotChangedError(message)
            except OSError as exc:
                if exc.errno in _PLUGIN_MUTATION_ERRNOS:
                    raise PluginSnapshotChangedError(message) from exc
                raise
        finally:
            os.close(fd)
        suffix = "/".join(reversed(unresolved))
        return f"missing:{candidate}:{suffix}:{_stat_generation(info)}"


def _missing_generation_at(directory_fd: int, target: str) -> str:
    """Identify a missing followed target through its existing parent."""
    target_path = Path(target)
    candidate = target_path.parent
    unresolved = [target_path.name]
    flags = _directory_flags()
    while True:
        candidate_name = os.fspath(candidate) or "."
        try:
            fd = os.open(candidate_name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            if candidate == candidate.parent:
                raise CodexServerError(
                    f"cannot identify missing plugin symlink target {target!r}"
                ) from None
            unresolved.append(candidate.name)
            candidate = candidate.parent
            continue
        except OSError as exc:
            if exc.errno in _PLUGIN_PATH_RACE_ERRNOS:
                raise PluginSnapshotChangedError(
                    f"plugin symlink target changed while being read: {target!r}"
                ) from exc
            raise CodexServerError(
                f"cannot inspect parent of plugin symlink target {target!r}: {exc}"
            ) from exc
        message = f"plugin symlink target changed while being read: {target!r}"
        try:
            try:
                info = os.fstat(fd)
                _require_missing_path_at(
                    fd,
                    unresolved[-1],
                    message,
                    follow_symlinks=False,
                )
                _require_missing_path_at(directory_fd, target, message)
                _require_unchanged_path_at(
                    directory_fd,
                    candidate_name,
                    info,
                    message,
                    follow_symlinks=False,
                )
                if _stat_generation(os.fstat(fd)) != _stat_generation(info):
                    raise PluginSnapshotChangedError(message)
            except OSError as exc:
                if exc.errno in _PLUGIN_MUTATION_ERRNOS:
                    raise PluginSnapshotChangedError(message) from exc
                raise
        finally:
            os.close(fd)
        suffix = "/".join(reversed(unresolved))
        return f"missing:{candidate}:{suffix}:{_stat_generation(info)}"


def _read_bounded(fd: int, limit: int) -> bytes:
    """Read at most ``limit`` bytes and reject one additional byte."""
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise CodexServerError("Codex plugin input exceeds the safe size limit")
    return data


def _directory_flags() -> int:
    """Return fail-closed flags for opening a directory without following."""
    flags = os.O_RDONLY | _no_follow_flag()
    directory = getattr(os, "O_DIRECTORY", None)
    if directory is None:
        raise CodexServerError(
            "safe Codex plugin scanning requires O_DIRECTORY support"
        )
    return flags | directory


def _no_follow_flag() -> int:
    """Return no-follow support or fail closed before opening input paths."""
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise CodexServerError("safe Codex plugin scanning requires O_NOFOLLOW support")
    return flag


def _stat_generation(info: os.stat_result) -> str:
    """Return an identity that changes when a file is replaced or rewritten."""
    return ":".join(
        str(value)
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    )
