"""Repository and directory selection for Amux display views."""

from __future__ import annotations

import argparse
from pathlib import Path

from .model import Agent


def directory_argument(value: str) -> str:
    """Resolve an existing directory supplied on the command line."""
    try:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            return str(path)
    except (OSError, RuntimeError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    raise argparse.ArgumentTypeError(f"not a directory: {value!r}")


def repository_name(cwd: str) -> str:
    """Find the shared repository's directory name, including linked worktrees."""
    if not cwd:
        return ""
    try:
        path = Path(cwd).resolve()
        for root in (path, *path.parents):
            marker = root / ".git"
            if marker.is_dir():
                return root.name
            if marker.is_file():
                pointer = marker.read_text().strip()
                if not pointer.startswith("gitdir: "):
                    return ""
                git_dir = (root / pointer.removeprefix("gitdir: ")).resolve()
                common_file = git_dir / "commondir"
                if not common_file.is_file():
                    return root.name
                common = (git_dir / common_file.read_text().strip()).resolve()
                return common.parent.name if common.name == ".git" else common.name
    except (OSError, RuntimeError, UnicodeError):
        return ""
    return ""


def select_agents(
    agents: list[Agent], repo: str | None, directory: str | None,
) -> list[Agent]:
    """Apply exact repository-name and directory-subtree filters together."""
    selected = []
    names: dict[str, str] = {}
    for agent in agents:
        if repo is not None and agent.repo != repo:
            if agent.cwd not in names:
                names[agent.cwd] = repository_name(agent.cwd)
            if names[agent.cwd] != repo:
                continue
        if directory:
            if not agent.cwd:
                continue
            try:
                Path(agent.cwd).resolve().relative_to(directory)
            except (ValueError, OSError, RuntimeError):
                continue
        selected.append(agent)
    return selected
