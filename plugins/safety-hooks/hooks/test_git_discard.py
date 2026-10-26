"""git reset --hard and git clean -f discard work that git cannot recover."""
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
])
def test_allows_commands_that_keep_work(command):
    assert not check_git_checkout_command(command)[0]
