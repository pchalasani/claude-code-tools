"""git reset --hard, git clean -f and git restore discard work that git cannot recover."""
import pytest
from git_checkout_safety_hook import check_git_checkout_command


@pytest.mark.parametrize("command", [
    "git reset --hard",
    "git reset --hard HEAD~1",
    "git -C /tmp/repo reset --hard origin/main",
    "git clean -f",
    "git clean -fdx",
    "git clean -d --force",
    "git clean --forc",
    "git status && git reset --hard",
    "git reset --har HEAD",
    "git reset --h HEAD",
    "git clean -f -e -n",
    "git clean -fe -n",
    "git clean -fen",
    "git clean -f --exclude -n",
    "git clean -f -n --no-dry-run",
    "git clean -f --dry-run --no-dry",
])
def test_blocks_commands_that_discard_work(command):
    assert check_git_checkout_command(command)[0]


@pytest.mark.parametrize("command", [
    "git reset",
    "git reset --soft HEAD~1",
    "git reset HEAD -- file.py",
    "git reset -- --hard",
    "git clean -n",
    "git clean -fdn",
    "git clean -f --dry-run",
    "git clean",
    "echo git reset --hard",
    "git clean -f -e foo -n",
    "git clean -f --exclude=*.log -n",
    "git clean -f --no-dry-run --dry-run",
])
def test_allows_commands_that_keep_work(command):
    assert not check_git_checkout_command(command)[0]


@pytest.mark.parametrize("command", [
    "git restore file.py",
    "git restore .",
    "git restore -- file.py",
    "git restore --worktree file.py",
    "git restore --work file.py",
    "git restore -W file.py",
    "git restore --staged --worktree .",
    "git restore -SW .",
    "git restore --source=HEAD~1 file.py",
    "git restore -s HEAD~1 file.py",
    "git restore -sSTABLE file.py",
    "git restore -p file.py",
    "git -C /tmp/repo restore .",
    "git add -A && git restore .",
    "git restore --pathspec-from-file -S",
    "git restore --pathspec-from-file paths.txt",
])
def test_blocks_restore_of_the_working_tree(command):
    assert check_git_checkout_command(command)[0]


@pytest.mark.parametrize("command", [
    "git restore --staged file.py",
    "git restore --stag file.py",
    "git restore -S file.py",
    "git restore --staged --source=HEAD~1 file.py",
    "git restore --staged -- --worktree",
    "git restore --staged --source HEAD~1 file.py",
    "git restore -S -s HEAD~1 file.py",
    "git restore -h",
    "git restore --help",
    "echo git restore .",
])
def test_allows_restore_of_the_index_only(command):
    assert not check_git_checkout_command(command)[0]


def test_danger_message_does_not_recommend_a_discarding_restore():
    reason = check_git_checkout_command("git checkout -- file.py")[1]
    assert "git restore --staged" in reason
    assert "for clearer syntax" not in reason
