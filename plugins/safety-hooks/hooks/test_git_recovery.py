"""Exercise literal index recovery in real, temporary Git repositories."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import git_recovery
from git_recovery import safe_index_paths


def git(repo: Path, *args: str, data: bytes = b"") -> bytes:
    """Run a fixture command in the temporary repository."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=data,
        capture_output=True, check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an index with ordinary tracked files independent of user config."""
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git(tmp_path, "init", "-q")
    (tmp_path / "sub").mkdir()
    for path in ("live", "lost", "sub/nested file"):
        (tmp_path / path).write_text("index content\n", encoding="utf-8")
    git(tmp_path, "add", "--", "live", "lost", "sub/nested file")
    (tmp_path / "lost").unlink()
    return tmp_path


def test_only_safe_index_files(repo: Path) -> None:
    """Clean, missing, and newly staged content can be restored together."""
    paths = ["./live", "lost", "sub/nested file", str(repo / "live")]
    assert safe_index_paths(str(repo), paths)
    assert safe_index_paths(str(repo / "sub"), ["nested file"])
    (repo / "live").write_bytes(b"unstaged edit\n")
    assert not safe_index_paths(str(repo), paths)
    git(repo, "add", "--", "live")
    assert safe_index_paths(str(repo), paths)


def test_git_probes_share_one_deadline(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop examining files when the remaining time for all probes runs out."""
    times = iter((0.0, 0.5, 1.25, 2.25))
    monkeypatch.setattr(
        git_recovery, "monotonic", lambda: next(times, 2.25), raising=False,
    )
    with patch.object(subprocess, "check_output", wraps=subprocess.check_output) as run:
        assert not safe_index_paths(str(repo), ["lost", "live"])
    assert [call.kwargs["timeout"] for call in run.call_args_list] == [1.5, 0.75]


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_special_index_flags(repo: Path, flag: str) -> None:
    """Special index bits can hide edits from ordinary status checks."""
    git(repo, "update-index", flag, "live")
    (repo / "live").write_bytes(b"hidden edits")
    assert not safe_index_paths(str(repo), ["live"])
    (repo / "live").unlink()
    assert not safe_index_paths(str(repo), ["live"])


def test_intent_to_add_and_unmerged(repo: Path) -> None:
    """Neither placeholder nor conflict entries supply an ordinary blob."""
    (repo / "intent").touch()
    git(repo, "add", "-N", "intent")
    (repo / "intent").unlink()
    assert not safe_index_paths(str(repo), ["intent"])
    oid = git(repo, "rev-parse", ":lost").strip()
    git(repo, "update-index", "--force-remove", "lost")
    git(repo, "update-index", "--index-info", data=b"100644 " + oid + b" 2\tlost\n")
    assert not safe_index_paths(str(repo), ["lost"])


def test_unmatched_directories_and_ambiguous_paths(repo: Path) -> None:
    """Only ordinary named file operands with a known repository qualify."""
    for path in ("absent", "sub", "**", ":(top)lost", "sub/../lost", "./", "$name"):
        assert not safe_index_paths(str(repo), [path])
    assert not safe_index_paths(str(repo), ["lost", "untracked"])
    assert not safe_index_paths(str(repo), [])
    assert not safe_index_paths(None, ["lost"])
    assert not safe_index_paths(str(repo / "absent"), ["lost"])


def test_directory_replaced_by_symlink_or_file(repo: Path) -> None:
    """Recreating a tracked child must preserve anything at its parents."""
    (repo / "sub/nested file").unlink()
    (repo / "sub").rmdir()
    assert safe_index_paths(str(repo), ["sub/nested file"])
    (repo / "sub").symlink_to("missing-directory")
    assert not safe_index_paths(str(repo), ["sub/nested file"])
    (repo / "sub").unlink()
    (repo / "sub").touch()
    assert not safe_index_paths(str(repo), ["sub/nested file"])


def test_replaced_file_and_mode_changes(repo: Path) -> None:
    """A mode change, symlink or untracked directory can hold local work."""
    (repo / "live").chmod(0o755)
    assert not safe_index_paths(str(repo), ["live"])
    (repo / "lost").symlink_to("absent")
    assert not safe_index_paths(str(repo), ["lost"])
    git(repo, "add", "--", "lost")
    (repo / "lost").unlink()
    assert not safe_index_paths(str(repo), ["lost"])
    (repo / "live").unlink()
    (repo / "live").mkdir()
    (repo / "live/local").touch()
    assert not safe_index_paths(str(repo), ["live"])
