"""git reset --hard, git clean -f and git restore discard work that git cannot recover."""
import pytest
import git_checkout_safety_hook
from git_checkout_safety_hook import _parse_git_help, check_git_checkout_command


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
    "git restore -S --no-staged file.py",
    "git restore --staged --worktree --no-staged file.py",
])
def test_blocks_restore_of_the_working_tree(command):
    assert check_git_checkout_command(command)[0]


@pytest.mark.parametrize("command", [
    "git restore --staged file.py",
    "git restore -S file.py",
    "git restore -W --no-worktree -S file.py",
    "git restore --staged --end-of-options -W",
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


@pytest.mark.parametrize("command, full_name", [
    ("git reset --har HEAD", "--hard"),
    ("git reset --so HEAD~1", "--soft"),
    ("git restore --stag file.py", "--staged"),
    ("git restore --work file.py", "--worktree"),
    ("git clean --forc", "--force"),
    ("git clean -f -n --no-dry", "--no-dry-run"),
])
def test_blocks_a_shortened_option_and_names_the_full_one(command, full_name):
    should_block, reason = check_git_checkout_command(command)
    assert should_block
    assert "Spell the option out: '{0}'".format(full_name) in reason


def test_names_each_option_a_shortened_name_can_mean():
    should_block, reason = check_git_checkout_command("git restore --s file.py")
    assert should_block
    assert "'--source', '--staged'" in reason


@pytest.mark.parametrize("command, option", [
    ("git clean -f --frobnicate -n", "--frobnicate"),
    ("git clean -Z", "-Z"),
])
def test_blocks_an_option_that_git_does_not_list(command, option):
    should_block, reason = check_git_checkout_command(command)
    assert should_block
    assert "lists no option '{0}'".format(option) in reason


def test_end_of_options_ends_the_options():
    assert check_git_checkout_command("git clean -f --end-of-options -n")[0]


def test_blocks_when_git_option_list_cannot_be_read(monkeypatch):
    monkeypatch.setattr(
        git_checkout_safety_hook, "_git_options", lambda subcommand: None)
    should_block, reason = check_git_checkout_command("git reset --soft HEAD~1")
    assert should_block
    assert "Could not read git's option list" in reason


def test_reads_each_option_and_its_value_from_git_help():
    long_options, short_options = _parse_git_help(
        "usage: git x [<options>]\n"
        "   or: git x --other\n"
        "\n"
        "    -s, --[no-]source <tree-ish>\n"
        "                          which tree-ish to checkout from\n"
        "    -d                    remove whole directories\n"
        "    -e, --exclude <pattern>\n"
        "    --[no-]recurse-submodules[=<checkout>]\n"
        "    -u[<mode>]            show untracked files\n")
    assert long_options == {
        "--source": "required", "--no-source": None, "--exclude": "required",
        "--recurse-submodules": "optional", "--no-recurse-submodules": None,
    }
    assert short_options == {
        "s": "required", "d": None, "e": "required", "u": "optional",
    }


def test_danger_message_does_not_recommend_a_discarding_restore():
    reason = check_git_checkout_command("git checkout -- file.py")[1]
    assert "git restore --staged" in reason
    assert "for clearer syntax" not in reason
