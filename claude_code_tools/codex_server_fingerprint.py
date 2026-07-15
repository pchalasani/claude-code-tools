"""Safe snapshots of Codex plugin configuration and cache inputs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from claude_code_tools.codex_server_models import CodexServerError, ServerPaths


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
    """Safely snapshot plugin configuration and resolved cache artifacts."""
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
    artifacts = hashlib.sha256()
    tree_generations: list[str] = []
    for relative in (Path("plugins"), Path("cache/remote_plugin_catalog")):
        tree_generations.append(
            hash_plugin_tree(paths.codex_home / relative, relative, artifacts)
        )
    artifact_digest = artifacts.hexdigest()
    fingerprint = hashlib.sha256(configuration + artifact_digest.encode()).hexdigest()
    generation = hashlib.sha256(
        "\0".join(
            [
                *configuration_generations,
                *tree_generations,
                artifact_digest,
            ]
        ).encode()
    ).hexdigest()
    return PluginSnapshot(fingerprint=fingerprint, generation=generation)


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
    return {
        "features": features,
        "marketplaces": config.get("marketplaces", {}),
        "plugins": config.get("plugins", {}),
    }


def read_plugin_configuration(path: Path) -> tuple[dict[str, object], str]:
    """Read bounded TOML from a nonblocking, no-follow regular descriptor."""
    flags = os.O_RDONLY | os.O_NONBLOCK | _no_follow_flag()
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return {}, _missing_generation(path)
    except OSError as exc:
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
            raise CodexServerError(
                f"Codex plugin configuration changed while being read: {path}"
            )
    except OSError as exc:
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
        return _missing_generation(root)
    except OSError as exc:
        raise CodexServerError(
            f"cannot inspect Codex plugin cache {root!r}: {exc}"
        ) from exc
    generation = hashlib.sha256()
    budget = [0, 0]
    try:
        _hash_directory(root_fd, label, digest, generation, budget, 0)
    except OSError as exc:
        raise CodexServerError(
            f"cannot inspect Codex plugin cache {root!r}: {exc}"
        ) from exc
    finally:
        os.close(root_fd)
    return generation.hexdigest()


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
    _update_entry(digest, generation, b"directory", relative, initial_info)
    with os.scandir(directory_fd) as stream:
        names: list[str] = []
        for child in stream:
            budget[0] += 1
            if budget[0] > PLUGIN_TREE_MAX_ENTRIES:
                raise CodexServerError(
                    "Codex plugin cache contains too many entries to snapshot safely"
                )
            names.append(child.name)
    names.sort(key=os.fsencode)
    for name in names:
        child_relative = relative / name
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
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
                if _stat_generation(os.fstat(child_fd)) != _stat_generation(info):
                    raise CodexServerError(
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
    if _stat_generation(os.fstat(directory_fd)) != _stat_generation(initial_info):
        raise CodexServerError(
            f"Codex plugin directory changed while being read: {relative!r}"
        )


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
            raise CodexServerError(
                f"Codex plugin artifact is not a regular file: {relative!r}"
            )
        if _stat_generation(initial_info) != _stat_generation(expected):
            raise CodexServerError(
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
        return
    if stat.S_ISDIR(expected.st_mode):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            if _stat_generation(os.fstat(fd)) != _stat_generation(expected):
                raise CodexServerError(
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
        return
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        initial_info = os.fstat(fd)
        if _stat_generation(initial_info) != _stat_generation(expected):
            raise CodexServerError(f"Codex plugin symlink target changed: {relative!r}")
        _hash_regular_descriptor(
            fd,
            relative / "<symlink-target>",
            initial_info,
            digest,
            generation,
            budget,
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
            raise CodexServerError(
                f"Codex plugin artifact changed while being read: {relative!r}"
            )
        digest.update(chunk)
        remaining -= len(chunk)
    digest.update(b"\0")
    if os.read(fd, 1):
        raise CodexServerError(
            f"Codex plugin artifact changed while being read: {relative!r}"
        )
    if _stat_generation(os.fstat(fd)) != _stat_generation(initial_info):
        raise CodexServerError(
            f"Codex plugin artifact changed while being read: {relative!r}"
        )


def _update_entry(
    digest: HashWriter,
    generation: HashWriter,
    kind: bytes,
    relative: Path,
    info: os.stat_result,
) -> None:
    """Add a typed pathname and its observed generation to both digests."""
    record = (
        kind
        + b":"
        + os.fsencode(str(relative))
        + b"\0"
        + _stat_generation(info).encode()
        + b"\0"
    )
    digest.update(record)
    generation.update(record)


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
            raise CodexServerError(
                f"cannot inspect parent of Codex plugin input {path}: {exc}"
            ) from exc
        try:
            info = os.fstat(fd)
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
            raise CodexServerError(
                f"cannot inspect parent of plugin symlink target {target!r}: {exc}"
            ) from exc
        try:
            info = os.fstat(fd)
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
