#!/usr/bin/env python3
"""
Unit tests for git_checkout_safety_hook.py

Tests cover:
    - Destructive checkouts that also mention "-b" somewhere
    - `git -C <dir> checkout` (the subcommand is not always argv[1])
    - Option tokens vs. substrings of ordinary filenames
    - Branch creation and --help staying exempt
    - The uncommitted-changes probe reading the repository the command targets
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_checkout_safety_hook import (
    check_git_checkout_command,
    parse_git_checkout,
)


class TestParseGitCheckout(unittest.TestCase):
    """Tests for parse_git_checkout() command splitting."""

    def test_plain_checkout(self):
        args, _ = parse_git_checkout("git checkout main")
        self.assertEqual(args, ["main"])

    def test_not_a_checkout(self):
        self.assertEqual(parse_git_checkout("git status"), (None, None))
        self.assertEqual(parse_git_checkout("ls checkout"), (None, None))
        self.assertEqual(parse_git_checkout(""), (None, None))

    def test_checkout_word_in_an_argument_is_not_the_subcommand(self):
        """A branch named "checkout" is not a checkout subcommand."""
        self.assertEqual(parse_git_checkout("git switch checkout"), (None, None))

    def test_dash_c_retargets_the_repository(self):
        """`git -C <dir>` runs in <dir>, so that is the repo to judge."""
        with tempfile.TemporaryDirectory() as tmp:
            args, repo_dir = parse_git_checkout(f"git -C {tmp} checkout main")
            self.assertEqual(args, ["main"])
            self.assertEqual(os.path.realpath(repo_dir), os.path.realpath(tmp))

    def test_dash_c_expands_an_ordinary_tilde_path(self):
        """An ordinary home-relative path can be resolved without a shell."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            repo = os.path.join(home, "repo")
            os.makedirs(repo)
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = home
            self.addCleanup(self._restore_home, old_home)
            args, repo_dir = parse_git_checkout(
                "git -C ~/repo checkout main"
            )
            self.assertEqual(args, ["main"])
            self.assertEqual(os.path.realpath(repo_dir), os.path.realpath(repo))

    @staticmethod
    def _restore_home(old_home):
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

    def test_dash_c_does_not_expand_quoted_or_escaped_tildes(self):
        with tempfile.TemporaryDirectory() as tmp:
            literal_repo = os.path.join(tmp, "~", "repo")
            fake_home = os.path.join(tmp, "home")
            os.makedirs(literal_repo)
            os.makedirs(os.path.join(fake_home, "repo"))
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = fake_home
            self.addCleanup(self._restore_home, old_home)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            self.addCleanup(os.chdir, old_cwd)

            for command in (
                'git -C "~/repo" checkout main',
                r"git -C \~/repo checkout main",
            ):
                _, repo_dir = parse_git_checkout(command)
                self.assertEqual(
                    os.path.realpath(repo_dir), os.path.realpath(literal_repo)
                )

    def test_absolute_path_to_git_binary(self):
        args, _ = parse_git_checkout("/usr/bin/git checkout main")
        self.assertEqual(args, ["main"])

    def test_unresolvable_target_reports_unknown_not_cwd(self):
        """An unexpanded -C value must not fall back to the caller's repo."""
        args, repo_dir = parse_git_checkout('git -C "$REPO" checkout main')
        self.assertEqual(args, ["main"])
        self.assertIsNone(repo_dir)

    def test_work_tree_reports_unknown(self):
        _, repo_dir = parse_git_checkout("git --work-tree=/somewhere checkout main")
        self.assertIsNone(repo_dir)

    def test_attr_source_value_does_not_hide_checkout(self):
        for command in (
            "git --attr-source HEAD checkout -f",
            "git --attr-source=HEAD checkout -f",
        ):
            with self.subTest(command=command):
                args, _ = parse_git_checkout(command)
                self.assertEqual(args, ["-f"])
                blocked, _ = check_git_checkout_command(command)
                self.assertTrue(blocked)

    def test_terminal_global_options_do_not_dispatch_checkout(self):
        for option in (
            "-h",
            "-v",
            "--help",
            "--version",
            "--html-path",
            "--man-path",
            "--info-path",
        ):
            with self.subTest(option=option):
                command = f"git {option} checkout -f"
                self.assertEqual(parse_git_checkout(command), (None, None))
                self.assertFalse(check_git_checkout_command(command)[0])


class TestAlwaysBlocked(unittest.TestCase):
    """Destructive forms that must be blocked regardless of repo state."""

    def test_force_checkout(self):
        blocked, reason = check_git_checkout_command("git checkout -f")
        self.assertTrue(blocked)
        self.assertIn("FORCES checkout", reason)

    def test_force_long_form(self):
        blocked, _ = check_git_checkout_command("git checkout --force main")
        self.assertTrue(blocked)

    def test_force_long_option_abbreviations(self):
        for option in ("--f", "--fo", "--for", "--forc"):
            with self.subTest(option=option):
                blocked, reason = check_git_checkout_command(
                    f"git checkout {option} main"
                )
                self.assertTrue(blocked)
                self.assertIn("DANGEROUS", reason)

    def test_assignment_prefixed_force_checkout(self):
        for command in (
            "MODE=prod git checkout -f",
            "env MODE=prod git checkout -f",
            "env -i -u DEBUG MODE=prod git checkout -f",
        ):
            with self.subTest(command=command):
                self.assertTrue(check_git_checkout_command(command)[0])

    def test_env_split_string_force_checkout(self):
        for command in (
            "env -S 'git checkout -f'",
            "env --split-string='git checkout -f'",
            "env -iS 'git checkout -f'",
            "env -S'git checkout -f'",
        ):
            with self.subTest(command=command):
                self.assertTrue(check_git_checkout_command(command)[0])

    def test_env_split_string_can_begin_with_env_options(self):
        for command in (
            "env -S '-C /tmp git checkout -f'",
            "env -S '-i git checkout -f'",
            "env -S '-u DEBUG git checkout -f'",
            "env -S '-- git checkout -f'",
        ):
            with self.subTest(command=command):
                self.assertTrue(check_git_checkout_command(command)[0])

    def test_clustered_env_value_options_before_force_checkout(self):
        for command in (
            "env -a harmless git checkout -f",
            "env -aharmless git checkout -f",
            "env --argv0 harmless git checkout -f",
            "env -iC /tmp git checkout -f",
            "env -iu PATH git checkout -f",
            "env -iC/tmp git checkout -f",
            "env -uSOMETHING git checkout -f",
            "env -P/usr/bin git checkout -f",
        ):
            with self.subTest(command=command):
                self.assertTrue(check_git_checkout_command(command)[0])

    def test_checkout_dot(self):
        blocked, reason = check_git_checkout_command("git checkout .")
        self.assertTrue(blocked)
        self.assertIn("DISCARD ALL", reason)

    def test_checkout_double_dash_dot(self):
        blocked, _ = check_git_checkout_command("git checkout HEAD -- .")
        self.assertTrue(blocked)

    def test_checkout_double_dash_path(self):
        blocked, _ = check_git_checkout_command("git checkout HEAD -- src/app.ts")
        self.assertTrue(blocked)

    # --- Regressions: these are the forms a substring test for "-b" let through

    def test_force_combined_with_branch_creation(self):
        """`-f` discards work even when a branch is also being created."""
        blocked, reason = check_git_checkout_command("git checkout -f -b feature")
        self.assertTrue(blocked, "-f must be blocked even alongside -b")
        self.assertIn("FORCES checkout", reason)

    def test_force_in_a_short_option_cluster(self):
        blocked, _ = check_git_checkout_command("git checkout -fb feature")
        self.assertTrue(blocked, "-f inside a cluster must still be seen")

    def test_attached_branch_name_does_not_create_force_flag(self):
        """Characters in an attached -b argument are not more options."""
        blocked, _ = check_git_checkout_command("git checkout -bfeature")
        self.assertFalse(blocked)
        blocked, _ = check_git_checkout_command("git checkout -bforce")
        self.assertFalse(blocked)

    def test_option_before_attached_branch_name_is_still_seen(self):
        """Short options before -b remain active when its value is attached."""
        blocked, _ = check_git_checkout_command("git checkout -fbfeature")
        self.assertTrue(blocked)

    def test_checkout_dot_with_branch_creation(self):
        blocked, _ = check_git_checkout_command("git checkout . -b feature")
        self.assertTrue(blocked, "'.' pathspec must be blocked even alongside -b")

    def test_filename_containing_dash_b_is_not_an_option(self):
        """"-b" inside a filename must not disarm the guard."""
        blocked, _ = check_git_checkout_command(
            "git checkout HEAD -- src/my-button.ts")
        self.assertTrue(blocked, "'my-button.ts' contains '-b' but is a filename")

    def test_branch_name_containing_dash_b_is_not_an_option(self):
        blocked, _ = check_git_checkout_command("git checkout -f my-branch")
        self.assertTrue(blocked)

    # --- Regressions: the `git -C <dir> <subcommand>` form

    def test_dash_c_force_checkout(self):
        blocked, reason = check_git_checkout_command(
            "git -C /some/other/repo checkout -f")
        self.assertTrue(blocked, "git -C <dir> checkout is still a checkout")
        self.assertIn("FORCES checkout", reason)

    def test_dash_c_checkout_dot(self):
        blocked, _ = check_git_checkout_command("git -C /some/other/repo checkout .")
        self.assertTrue(blocked)

    def test_compound_command_with_dash_c(self):
        blocked, _ = check_git_checkout_command(
            "echo hi && git -C /some/other/repo checkout -f")
        self.assertTrue(blocked)

    def test_quoted_dash_c_operators_are_not_command_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            for operator in (";", "&&", "|"):
                repo = os.path.join(tmp, f"repo{operator}name")
                os.makedirs(repo)
                command = f'git -C "{repo}" checkout -f'
                with self.subTest(operator=operator):
                    self.assertTrue(check_git_checkout_command(command)[0])

    def test_escaped_dash_c_operator_is_not_a_command_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo;name")
            os.makedirs(repo)
            escaped_repo = repo.replace(";", r"\;")
            command = f"git -C {escaped_repo} checkout -f"
            self.assertTrue(check_git_checkout_command(command)[0])

    def test_real_compound_operator_still_splits_commands(self):
        command = 'printf "%s" "safe;value"; git checkout -f'
        self.assertTrue(check_git_checkout_command(command)[0])

    def test_file_descriptor_redirection_is_not_a_command_separator(self):
        self.assertTrue(
            check_git_checkout_command("2>&1 git checkout -f")[0]
        )
        self.assertTrue(
            check_git_checkout_command("0<&1 git checkout -f")[0]
        )
        self.assertTrue(
            check_git_checkout_command("> /dev/null git checkout -f")[0]
        )
        self.assertTrue(
            check_git_checkout_command("2> /dev/null git checkout -f")[0]
        )
        self.assertTrue(
            check_git_checkout_command("< /dev/null git checkout -f")[0]
        )


class TestAllowed(unittest.TestCase):
    """Forms that must stay allowed."""

    def test_branch_creation(self):
        blocked, _ = check_git_checkout_command("git checkout -b feature")
        self.assertFalse(blocked)

    def test_help(self):
        self.assertFalse(check_git_checkout_command("git checkout --help")[0])
        self.assertFalse(check_git_checkout_command("git checkout -h")[0])

    def test_non_checkout_commands(self):
        self.assertFalse(check_git_checkout_command("git status")[0])
        self.assertFalse(check_git_checkout_command("git commit -m 'x'")[0])
        self.assertFalse(check_git_checkout_command("echo git checkout -f")[0])

    def test_assignment_prefixed_non_checkout_is_allowed(self):
        self.assertFalse(check_git_checkout_command("MODE=prod git status")[0])

    def test_unresolvable_target_is_blocked_without_wrong_repo_details(self):
        """An unknown target gets a generic warning and no caller-repo files."""
        blocked, reason = check_git_checkout_command(
            'git -C "$REPO" checkout main'
        )
        self.assertTrue(blocked)
        self.assertIn("Could not safely resolve the target repository", reason)
        self.assertNotIn("git_checkout_safety_hook.py", reason)
        blocked, _ = check_git_checkout_command('git -C "$REPO" checkout -f')
        self.assertTrue(blocked, "command-level checks still apply")

    def test_non_repository_target_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked, reason = check_git_checkout_command(
                f"git -C {directory} checkout main"
            )
        self.assertTrue(blocked)
        self.assertIn("Could not safely resolve", reason)

    def test_repository_routing_assignments_fail_closed(self):
        commands = (
            "GIT_DIR=/other/.git GIT_WORK_TREE=/other git checkout main",
            "env GIT_DIR=/other/.git git checkout main",
            "env -S 'GIT_WORK_TREE=/other git checkout main'",
        )
        for command in commands:
            with self.subTest(command=command):
                blocked, reason = check_git_checkout_command(command)
                self.assertTrue(blocked)
                self.assertIn("Could not safely resolve", reason)


def _git(*args: str, cwd: str) -> None:
    """Run Git quietly in a test repository."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_environment(),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _git_environment() -> dict[str, str]:
    """Return an environment isolated from Git routing and user config."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    return environment


def _commit_file(repo: str, filename: str, content: str) -> str:
    """Create and commit one tracked file, returning its absolute path."""
    path = os.path.join(repo, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    _git("add", filename, cwd=repo)
    _git(
        "-c",
        "user.name=Safety Hooks Test",
        "-c",
        "user.email=safety-hooks@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "test fixture",
        cwd=repo,
    )
    return path


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class TestUncommittedChangesProbeTargetsTheRightRepo(unittest.TestCase):
    """The warning must describe the repo the command targets, not the cwd."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "other-repo")
        os.makedirs(self.repo)
        _git("init", cwd=self.repo)
        dirty_file = _commit_file(
            self.repo, "dirty-file.txt", "committed contents\n"
        )
        with open(dirty_file, "w", encoding="utf-8") as handle:
            handle.write("uncommitted contents\n")

        self.clean = os.path.join(self.tmp, "clean-cwd")
        os.makedirs(self.clean)
        _git("init", cwd=self.clean)
        self.clean_file = _commit_file(
            self.clean, "local-noise.txt", "committed contents\n"
        )

        self.original_cwd = os.getcwd()
        os.chdir(self.clean)
        self.addCleanup(os.chdir, self.original_cwd)

    def test_fixture_git_environment_is_sanitized(self):
        environment = _git_environment()
        self.assertEqual(environment.get("PATH"), os.environ.get("PATH"))
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        inherited_git_names = {
            name for name in environment if name.startswith("GIT_")
        }
        self.assertEqual(
            inherited_git_names,
            {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"},
        )

    def test_warns_about_the_target_repo(self):
        blocked, reason = check_git_checkout_command(
            f"git -C {self.repo} checkout main")
        self.assertTrue(blocked, "the target repo has uncommitted changes")
        self.assertIn("dirty-file.txt", reason)

    def test_env_chdir_warns_for_dirty_target_from_clean_caller(self):
        blocked, reason = check_git_checkout_command(
            f"env -C {self.repo} git checkout main"
        )
        self.assertTrue(blocked)
        self.assertIn("dirty-file.txt", reason)

    def test_clustered_env_chdir_warns_for_dirty_target(self):
        blocked, reason = check_git_checkout_command(
            f"env -iC {self.repo} git checkout main"
        )
        self.assertTrue(blocked)
        self.assertIn("dirty-file.txt", reason)

    def test_split_string_chdir_warns_for_dirty_target(self):
        blocked, reason = check_git_checkout_command(
            f"env -S '-C {self.repo} git checkout main'"
        )
        self.assertTrue(blocked)
        self.assertIn("dirty-file.txt", reason)

    def test_attached_dash_c_warns_for_dirty_target_from_clean_caller(self):
        blocked, reason = check_git_checkout_command(
            f"git -C{self.tmp} -Cother-repo checkout main"
        )
        self.assertTrue(blocked)
        self.assertIn("dirty-file.txt", reason)

    def test_clean_target_is_allowed_even_from_a_dirty_cwd(self):
        with open(self.clean_file, "w", encoding="utf-8") as handle:
            handle.write("only in the cwd\n")
        blocked, _ = check_git_checkout_command(
            f"git -C {self.repo} checkout main")
        self.assertTrue(blocked, "target repo is the dirty one here")

        # And the mirror image: a clean target must not inherit the cwd's dirt.
        pristine = os.path.join(self.tmp, "pristine")
        os.makedirs(pristine)
        _git("init", cwd=pristine)
        blocked, _ = check_git_checkout_command(
            f"git -C {pristine} checkout main")
        self.assertFalse(blocked, "clean target must not inherit the cwd's dirt")

    def test_attached_dash_c_allows_clean_target_from_dirty_caller(self):
        with open(self.clean_file, "w", encoding="utf-8") as handle:
            handle.write("only in the cwd\n")
        pristine = os.path.join(self.tmp, "pristine-attached")
        os.makedirs(pristine)
        _git("init", cwd=pristine)
        blocked, _ = check_git_checkout_command(
            f"git -C{self.tmp} -Cpristine-attached checkout main"
        )
        self.assertFalse(blocked)

    def test_env_chdir_allows_clean_target_from_dirty_caller(self):
        with open(self.clean_file, "w", encoding="utf-8") as handle:
            handle.write("only in the cwd\n")
        pristine = os.path.join(self.tmp, "pristine-env")
        os.makedirs(pristine)
        _git("init", cwd=pristine)
        blocked, _ = check_git_checkout_command(
            f"env --chdir={pristine} git checkout main"
        )
        self.assertFalse(blocked)

    def test_split_string_chdir_allows_clean_target(self):
        with open(self.clean_file, "w", encoding="utf-8") as handle:
            handle.write("only in the cwd\n")
        pristine = os.path.join(self.tmp, "pristine-split-string")
        os.makedirs(pristine)
        _git("init", cwd=pristine)
        blocked, _ = check_git_checkout_command(
            f"env -S '--chdir={pristine} git checkout main'"
        )
        self.assertFalse(blocked)

    def test_branch_reset_checks_dirty_work(self):
        for option in ("-B feature", "-Bfeature"):
            with self.subTest(option=option):
                blocked, reason = check_git_checkout_command(
                    f"git -C {self.repo} checkout {option}"
                )
                self.assertTrue(blocked)
                self.assertIn("dirty-file.txt", reason)


if __name__ == "__main__":
    unittest.main()
