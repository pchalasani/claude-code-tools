"""Project filters keep linked worktrees and picker refreshes consistent."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from claude_code_tools.amux import cli
from claude_code_tools.amux.filters import directory_argument, select_agents
from claude_code_tools.amux.model import Agent


def agent(cwd: Path, repo: str = "", pane: str = "test:1.1") -> Agent:
    """Make a live-row fixture without touching agent processes."""
    return Agent(pane, "test", "claude", cwd=str(cwd), repo=repo)


def test_repository_filter_includes_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    linked = tmp_path / "feature-tree"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid", "commit", "-qm", "initial",
        "--allow-empty",
    ], check=True)
    subprocess.run([
        "git", "-C", str(repo), "worktree", "add", "-qb", "feature", str(linked),
    ], check=True)
    (linked / "nested").mkdir()
    rows = [agent(repo, "project"), agent(linked / "nested", "feature-tree"),
            agent(tmp_path / "unrelated", "project-other")]
    assert select_agents(rows, "project", None) == rows[:2]
    assert select_agents(rows, "feature-tree", None) == [rows[1]]
    assert select_agents(rows, "Project", None) == []


def test_directory_boundary_symlink_and_combination(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    (repo / "nested").mkdir(parents=True)
    sibling = tmp_path / "project-other"
    sibling.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    rows = [agent(repo, "project"), agent(repo / "nested", "project"),
            agent(sibling, "project"), agent(repo, "other"),
            Agent("test:1.9", "test", "claude", repo="project")]
    selected = select_agents(rows, "project", directory_argument(str(alias)))
    assert selected == rows[:2]
    assert select_agents(rows, None, str(repo)) == rows[:2] + [rows[3]]


def test_directory_argument_rejects_file_or_missing(tmp_path: Path) -> None:
    regular = tmp_path / "file"
    regular.write_text("content")
    for path in [regular, tmp_path / "missing"]:
        with pytest.raises(argparse.ArgumentTypeError):
            directory_argument(str(path))


def test_directory_argument_expands_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert directory_argument(".") == str(tmp_path.resolve())


@pytest.mark.parametrize("command", ["list", "pick", "rows"])
def test_filters_combine_with_dormancy(command: str, tmp_path: Path) -> None:
    args = cli.build_parser().parse_args([
        command, "--project", "project", "--dir", str(tmp_path),
        "--dormant", "--sort", "oldest",
    ])
    old = agent(tmp_path, "project")
    old.state, old.last_input_at = "idle", 1
    busy = agent(tmp_path, "project", "test:1.2")
    busy.state, busy.last_input_at = "busy", 1
    other = agent(tmp_path, "other", "test:1.3")
    other.state, other.last_input_at = "idle", 1
    assert cli._display([busy, other, old], args) == [old]


@pytest.mark.parametrize("name", ["list", "pick", "scan", "rows"])
def test_repo_named_like_subcommand_defaults_to_picker(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []
    monkeypatch.setattr(cli.scan, "tmux_available", lambda: True)
    monkeypatch.setattr(cli, "cmd_pick", lambda args: seen.append(args) or 0)
    assert cli.main(["--repo", name]) == 0
    assert seen[0].repo == name


def test_picker_refresh_preserves_literal_filter_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "dir (x), {q} $HOME ' space"
    directory.mkdir()
    repo = "repo (x), {q} $(echo bad) '"
    row = agent(directory, repo)
    captured = {}
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/fzf")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_agents_for_display", lambda _: ([row], True))

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr(cli.subprocess, "run", run)
    args = cli.build_parser().parse_args([
        "pick", "--repo", repo, "--dir", str(directory),
    ])
    assert cli.cmd_pick(args) == 0
    binds = [x for x in captured["command"] if "reload" in x]
    assert len(binds) == 2
    assert all('--repo="$AMUX_RELOAD_REPO"' in x for x in binds)
    assert all('--dir="$AMUX_RELOAD_DIR"' in x for x in binds)
    assert all(repo not in x and str(directory) not in x for x in binds)
    assert captured["env"]["AMUX_RELOAD_REPO"] == repo
    assert captured["env"]["AMUX_RELOAD_DIR"] == str(directory)
