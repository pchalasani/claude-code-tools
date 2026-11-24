"""git reset --hard, git clean -f and git restore discard work that git cannot recover."""
import pytest
import git_checkout_safety_hook
from git_checkout_safety_hook import _parse_git_help, check_git_checkout_command


@pytest.mark.parametrize("command", [
    "git reset --hard",
    "git reset --hard HEAD~1",
    "git reset --merge",
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
    # clean.requireForce=false makes git delete without -f.
    "git clean",
    "git -c clean.requireForce=false clean -d",
    # `--` here is the value of -e, so -f and --no-dry-run are still options.
    "git clean -n -e -- -f --no-dry-run",
    # The shell drops comments and redirections; git never sees `-S` here.
    "git restore live # -S",
    "git restore live > -S",
    "git restore live 2> --staged",
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
    "echo git reset --hard",
    "git clean -f -e foo -n",
    "git clean -f --exclude=*.log -n",
    "git clean -f --no-dry-run --dry-run",
    "git restore --help-all",
    "git reset --soft HEAD~1 # --hard is not an option here",
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
    assert reason is not None
    assert "Spell the option out: '{0}'".format(full_name) in reason


def test_names_each_option_a_shortened_name_can_mean():
    should_block, reason = check_git_checkout_command("git restore --s file.py")
    assert should_block
    assert reason is not None
    assert "'--source', '--staged'" in reason


@pytest.mark.parametrize("command, option", [
    ("git clean -f --frobnicate -n", "--frobnicate"),
    ("git clean -Z", "-Z"),
])
def test_blocks_an_option_that_git_does_not_list(command, option):
    should_block, reason = check_git_checkout_command(command)
    assert should_block
    assert reason is not None
    assert "lists no option '{0}'".format(option) in reason


def test_end_of_options_ends_the_options():
    assert check_git_checkout_command("git clean -f --end-of-options -n")[0]


@pytest.mark.parametrize(("command", "blocked"), [
    ("git $'reset' --frobnicate", True),
    ("git reset $'--frobnicate'", True),
    ("$'git' reset --frobnicate", True),
    (r"git $'stat\x75s'", True),
    (r"git reset $'--s\x6fft'", True),
    (r"git log --format=$'subject\nbody'", False),
    (r"printf '%s' $'a\nb'", False),
    ("git $'status'", False),
])
def test_ansi_c_command_words(command: str, blocked: bool) -> None:
    """Plain words dispatch normally; opaque Git words require clarification."""
    assert check_git_checkout_command(command)[0] is blocked


@pytest.mark.parametrize(("command", "expected"), [
    ("2>errors git status", "git status"),
    ("12>>errors git status", "git status"),
    ("git status 2 >errors", "git status 2"),
    ("git status '2'>errors", "git status '2'"),
    ("git status '>' 2>errors", "git status '>'"),
    ("git status '2>errors'", "git status '2>errors'"),
])
def test_strips_only_shell_redirections(command: str, expected: str) -> None:
    assert git_checkout_safety_hook._without_shell_only_words(command) == expected


def test_blocks_when_git_option_list_cannot_be_read(monkeypatch):
    monkeypatch.setattr(
        git_checkout_safety_hook, "_git_options", lambda subcommand: None)
    should_block, reason = check_git_checkout_command("git reset --soft HEAD~1")
    assert should_block
    assert reason is not None
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
    assert reason is not None
    assert "git restore --staged" in reason
    assert "for clearer syntax" not in reason


def _repo_with_file(tmp_path, monkeypatch):
    """A repository with one committed file, `kept.txt`."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "kept.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "kept.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false",
                    "commit", "-q", "-m", "init"], check=True)
    return repo


@pytest.mark.parametrize("spelling", ["restore", "restore --", "checkout --"])
def test_recovering_a_deleted_file_is_allowed(tmp_path, monkeypatch, spelling):
    repo = _repo_with_file(tmp_path, monkeypatch)
    (repo / "kept.txt").unlink()
    command = "git -C {0} {1} kept.txt".format(repo, spelling)
    assert not check_git_checkout_command(command)[0]


@pytest.mark.parametrize("spelling", ["restore", "restore --", "checkout --"])
def test_restoring_over_an_edited_file_is_blocked(tmp_path, monkeypatch,
                                                  spelling):
    repo = _repo_with_file(tmp_path, monkeypatch)
    (repo / "kept.txt").write_text("edited\n")
    command = "git -C {0} {1} kept.txt".format(repo, spelling)
    assert check_git_checkout_command(command)[0]


def test_restoring_a_deleted_file_from_another_commit_is_blocked(
        tmp_path, monkeypatch):
    repo = _repo_with_file(tmp_path, monkeypatch)
    (repo / "kept.txt").unlink()
    command = "git -C {0} restore --source=HEAD kept.txt".format(repo)
    assert check_git_checkout_command(command)[0]


def test_recovery_is_not_allowed_after_a_cd(tmp_path, monkeypatch):
    """After `cd`, the file name would be checked against the wrong directory."""
    repo = _repo_with_file(tmp_path, monkeypatch)
    (repo / "kept.txt").unlink()
    command = "cd {0} && git restore kept.txt".format(repo)
    assert check_git_checkout_command(command)[0]


def test_hash_inside_a_file_name_is_not_a_comment(tmp_path, monkeypatch):
    """`a#b` is one file name to the shell, not `a` plus a comment."""
    repo = _repo_with_file(tmp_path, monkeypatch)
    import subprocess
    (repo / "kept.txt#2").write_text("v1\n")
    subprocess.run(["git", "-C", str(repo), "add", "kept.txt#2"], check=True)
    (repo / "kept.txt#2").write_text("edited\n")
    command = "git -C {0} restore kept.txt#2".format(repo)
    assert check_git_checkout_command(command)[0]


@pytest.mark.parametrize("extra", ["--icase-pathspecs ", "--glob-pathspecs "])
def test_no_recovery_with_pathspec_magic(tmp_path, monkeypatch, extra):
    repo = _repo_with_file(tmp_path, monkeypatch)
    (repo / "kept.txt").unlink()
    command = "git {0}-C {1} restore kept.txt".format(extra, repo)
    assert check_git_checkout_command(command)[0]


def test_no_recovery_when_shell_words_were_dropped(tmp_path, monkeypatch):
    repo = _repo_with_file(tmp_path, monkeypatch)
    (repo / "kept.txt").unlink()
    command = "git -C {0} restore kept.txt # recover it".format(repo)
    assert check_git_checkout_command(command)[0]
