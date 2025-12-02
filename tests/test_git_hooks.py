#!/usr/bin/env python3
"""
Comprehensive pytest tests for git-related hooks.

Tests the following hooks:
1. git_commit_block_hook.py - Speed bump pattern for git commits
2. git_add_block_hook.py - Complex pattern matching for git add
3. git_checkout_safety_hook.py - Safety checks with subprocess
"""

import os
import pytest
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

# Add the hooks directory to the path so we can import the hooks
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from git_commit_block_hook import check_git_commit_command
from git_add_block_hook import check_git_add_command
from git_checkout_safety_hook import check_git_checkout_command


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_git_dir(tmp_path):
    """Create a temporary directory for git operations and change to it."""
    original_dir = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    # Cleanup: change back to original directory
    os.chdir(original_dir)


@pytest.fixture(autouse=True)
def cleanup_flag_files(temp_git_dir):
    """Automatically cleanup all flag files after each test."""
    yield
    # Remove all flag files in the temp directory
    for flag_file in temp_git_dir.glob(".claude_git_*.flag"):
        flag_file.unlink()


# =============================================================================
# TESTS FOR git_commit_block_hook.py
# =============================================================================

class TestGitCommitBlockHook:
    """Tests for the git commit block hook (speed bump pattern)."""

    def test_non_git_commit_command_returns_false(self, temp_git_dir):
        """Non-git-commit commands should return (False, None)."""
        test_cases = [
            "ls -la",
            "echo hello",
            "git status",
            "git add file.py",
            "git push origin main",
            "git log",
            "git diff",
            "npm run test",
        ]

        for command in test_cases:
            should_block, reason = check_git_commit_command(command)
            assert should_block is False, f"Command '{command}' should not be blocked"
            assert reason is None, f"Command '{command}' should have no reason"

    def test_first_git_commit_attempt_blocks_and_creates_flag(self, temp_git_dir):
        """First git commit attempt should block and create flag file."""
        command = "git commit -m 'test commit'"
        flag_file = temp_git_dir / ".claude_git_commit_warning.flag"

        # Ensure flag file doesn't exist
        assert not flag_file.exists()

        # First attempt should block
        should_block, reason = check_git_commit_command(command)

        assert should_block is True
        assert reason is not None
        assert "blocked (first attempt)" in reason
        assert flag_file.exists(), "Flag file should be created"

    def test_second_git_commit_attempt_allows_and_removes_flag(self, temp_git_dir):
        """Second git commit attempt should allow and remove flag file."""
        command = "git commit -m 'test commit'"
        flag_file = temp_git_dir / ".claude_git_commit_warning.flag"

        # First attempt - blocks and creates flag
        check_git_commit_command(command)
        assert flag_file.exists()

        # Second attempt should allow and remove flag
        should_block, reason = check_git_commit_command(command)

        assert should_block is False
        assert reason is None
        assert not flag_file.exists(), "Flag file should be deleted"

    def test_git_commit_with_various_flags(self, temp_git_dir):
        """Test git commit with various flags still follows speed bump pattern."""
        test_cases = [
            "git commit -m 'message'",
            "git commit --amend",
            "git commit -a -m 'message'",
            "git commit --no-verify -m 'message'",
            "git commit -m 'multi word message' --author='John Doe'",
        ]

        for command in test_cases:
            flag_file = temp_git_dir / ".claude_git_commit_warning.flag"

            # Clean up any existing flag
            if flag_file.exists():
                flag_file.unlink()

            # First attempt should block
            should_block, reason = check_git_commit_command(command)
            assert should_block is True, f"First attempt of '{command}' should block"
            assert flag_file.exists()

            # Second attempt should allow
            should_block, reason = check_git_commit_command(command)
            assert should_block is False, f"Second attempt of '{command}' should allow"
            assert not flag_file.exists()

    def test_command_normalization(self, temp_git_dir):
        """Test that commands with extra spaces are normalized correctly."""
        test_cases = [
            "git    commit   -m   'test'",  # Multiple spaces
            "  git commit -m 'test'  ",     # Leading/trailing spaces
            "git\tcommit\t-m\t'test'",      # Tabs
        ]

        for command in test_cases:
            flag_file = temp_git_dir / ".claude_git_commit_warning.flag"

            # Clean up
            if flag_file.exists():
                flag_file.unlink()

            # Should still be recognized as git commit
            should_block, reason = check_git_commit_command(command)
            assert should_block is True, f"Command '{command}' should be recognized as git commit"
            assert flag_file.exists()

            # Cleanup for next test
            flag_file.unlink()

    def test_flag_file_cleanup_verification(self, temp_git_dir):
        """Verify that flag file is properly cleaned up in all scenarios."""
        command = "git commit -m 'test'"
        flag_file = temp_git_dir / ".claude_git_commit_warning.flag"

        # Multiple cycles should work correctly
        for _ in range(3):
            # First attempt
            check_git_commit_command(command)
            assert flag_file.exists()

            # Second attempt
            check_git_commit_command(command)
            assert not flag_file.exists()


# =============================================================================
# TESTS FOR git_add_block_hook.py
# =============================================================================

class TestGitAddBlockHook:
    """Tests for the git add block hook (complex pattern matching)."""

    def test_wildcard_patterns_blocked(self, temp_git_dir):
        """Wildcard patterns should be blocked."""
        test_cases = [
            "git add *.py",
            "git add *",
            "git add *.txt *.md",
            "git add src/*.js",
            "git add **/*.py",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is True, f"Wildcard command '{command}' should be blocked"
            assert reason is not None
            assert "wildcard" in reason.lower() or "BLOCKED" in reason

    def test_all_flag_variants_blocked(self, temp_git_dir):
        """All variants of -A, --all, -a flags should be blocked."""
        test_cases = [
            "git add -A",
            "git add --all",
            "git add -a",
            "git add -Am",  # Combined flags
            "git add -aA",  # Multiple combined
            "git add file.py -A",  # Flag after filename
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "BLOCKED" in reason

    def test_current_directory_dot_blocked(self, temp_git_dir):
        """'git add .' should be blocked."""
        test_cases = [
            "git add .",
            "git add . ",
            "git   add   .",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "BLOCKED" in reason

    def test_parent_directory_patterns_blocked(self, temp_git_dir):
        """Parent directory patterns like ../ should be blocked."""
        test_cases = [
            "git add ../",
            "git add ../file.py",
            "git add ../../src/",
            "git add ../../../",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "BLOCKED" in reason

    def test_directory_staging_first_attempt_blocks(self, temp_git_dir):
        """First attempt to stage a directory should block and create flag."""
        command = "git add src/"
        # Compute hash-based flag file name
        safe_name = hashlib.sha256("src".encode()).hexdigest()[:16]
        flag_file = temp_git_dir / f".claude_git_add_dir_{safe_name}.flag"

        # Ensure flag doesn't exist
        assert not flag_file.exists()

        with patch('subprocess.run') as mock_run:
            # Mock subprocess to return some files
            mock_result = MagicMock()
            mock_result.stdout = "src/file1.py\nsrc/file2.py\n"
            mock_run.return_value = mock_result

            should_block, reason = check_git_add_command(command)

            assert should_block is True
            assert reason is not None
            assert "blocked (first attempt)" in reason
            assert flag_file.exists(), "Flag file should be created"

    def test_directory_staging_second_attempt_allows(self, temp_git_dir):
        """Second attempt to stage a directory should allow and remove flag."""
        command = "git add src/"
        # Compute hash-based flag file name
        safe_name = hashlib.sha256("src".encode()).hexdigest()[:16]
        flag_file = temp_git_dir / f".claude_git_add_dir_{safe_name}.flag"

        with patch('subprocess.run') as mock_run:
            # Mock subprocess to return some files
            mock_result = MagicMock()
            mock_result.stdout = "src/file1.py\nsrc/file2.py\n"
            mock_run.return_value = mock_result

            # First attempt
            check_git_add_command(command)
            assert flag_file.exists()

            # Second attempt
            should_block, reason = check_git_add_command(command)

            assert should_block is False
            assert reason is None
            assert not flag_file.exists(), "Flag file should be removed"

    def test_directory_staging_with_nested_paths(self, temp_git_dir):
        """Test directory staging with nested paths."""
        test_cases = [
            ("git add src/components/", "src/components"),
            ("git add lib/utils/helpers/", "lib/utils/helpers"),
            ("git add tests/unit/", "tests/unit"),
        ]

        for command, dir_path in test_cases:
            # Compute hash-based flag file name
            safe_name = hashlib.sha256(dir_path.encode()).hexdigest()[:16]
            flag_file = temp_git_dir / f".claude_git_add_dir_{safe_name}.flag"

            with patch('subprocess.run') as mock_run:
                mock_result = MagicMock()
                mock_result.stdout = "file1.py\nfile2.py\n"
                mock_run.return_value = mock_result

                # First attempt should create the correct flag file
                should_block, reason = check_git_add_command(command)

                assert should_block is True
                assert flag_file.exists(), f"Expected flag file for {dir_path} should exist"

                # Cleanup
                flag_file.unlink()

    def test_directory_staging_file_list_display(self, temp_git_dir):
        """Test that directory staging shows file list in the warning."""
        command = "git add src/"

        with patch('subprocess.run') as mock_run:
            # Mock subprocess to return multiple files
            mock_result = MagicMock()
            files = "\n".join([f"src/file{i}.py" for i in range(5)])
            mock_result.stdout = files
            mock_run.return_value = mock_result

            should_block, reason = check_git_add_command(command)

            assert should_block is True
            assert "Files that would be staged:" in reason
            assert "src/file0.py" in reason
            assert "src/file4.py" in reason

    def test_directory_staging_many_files_truncated(self, temp_git_dir):
        """Test that directory staging truncates long file lists."""
        command = "git add src/"

        with patch('subprocess.run') as mock_run:
            # Mock subprocess to return many files
            mock_result = MagicMock()
            files = "\n".join([f"src/file{i}.py" for i in range(20)])
            mock_result.stdout = files
            mock_run.return_value = mock_result

            should_block, reason = check_git_add_command(command)

            assert should_block is True
            assert "and 10 more files" in reason or "and 10 more" in reason.lower()

    def test_git_commit_a_without_m_blocked(self, temp_git_dir):
        """'git commit -a' without -m should be blocked."""
        test_cases = [
            "git commit -a",
            "git commit -av",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "git commit -a" in reason

    def test_git_commit_a_with_amend_allowed(self, temp_git_dir):
        """'git commit -a --amend' is allowed (amending doesn't need -m)."""
        # --amend doesn't require a message, so this should be allowed
        command = "git commit -a --amend"
        should_block, reason = check_git_add_command(command)
        assert should_block is False, f"Command '{command}' should be allowed (--amend doesn't need -m)"

    def test_git_commit_a_with_m_allowed(self, temp_git_dir):
        """'git commit -a -m' should be allowed (has message flag)."""
        test_cases = [
            "git commit -a -m 'message'",
            "git commit -am 'message'",
            "git commit -m 'message' -a",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is False, f"Command '{command}' should be allowed"

    def test_safe_git_add_patterns_allowed(self, temp_git_dir):
        """Safe git add patterns should be allowed."""
        test_cases = [
            "git add file.py",
            "git add file1.py file2.py file3.py",
            "git add src/components/Button.tsx",
            "git add -u",  # Update tracked files only
            "git add --update",
            "git add -p file.py",  # Patch mode
            "git add --patch file.py",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is False, f"Safe command '{command}' should be allowed"
            assert reason is None

    def test_non_git_add_commands_allowed(self, temp_git_dir):
        """Non-git-add commands should be allowed."""
        test_cases = [
            "ls -la",
            "git status",
            "git commit -m 'message'",
            "git push",
            "npm install",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is False, f"Command '{command}' should be allowed"

    def test_command_normalization_git_add(self, temp_git_dir):
        """Test command normalization with extra whitespace."""
        # These should all be treated the same as "git add *"
        test_cases = [
            "git   add   *",
            "  git add *  ",
            "git\tadd\t*",
        ]

        for command in test_cases:
            should_block, reason = check_git_add_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"

    def test_directory_staging_subprocess_error_handling(self, temp_git_dir):
        """Test that directory staging handles subprocess errors gracefully."""
        command = "git add src/"

        with patch('subprocess.run') as mock_run:
            # Simulate subprocess error
            mock_run.side_effect = Exception("Subprocess error")

            should_block, reason = check_git_add_command(command)

            # Should still block but with generic warning
            assert should_block is True
            assert reason is not None
            assert "blocked (first attempt)" in reason


# =============================================================================
# TESTS FOR git_checkout_safety_hook.py
# =============================================================================

class TestGitCheckoutSafetyHook:
    """Tests for the git checkout safety hook (subprocess checks)."""

    def test_non_checkout_commands_return_false(self):
        """Non-checkout commands should return (False, None)."""
        test_cases = [
            "ls -la",
            "git status",
            "git add file.py",
            "git commit -m 'test'",
            "git push",
            "git branch",
            "git log",
        ]

        for command in test_cases:
            should_block, reason = check_git_checkout_command(command)
            assert should_block is False, f"Command '{command}' should not be blocked"
            assert reason is None

    def test_checkout_with_b_flag_allowed(self):
        """Checkout with -b flag (create new branch) should be allowed."""
        test_cases = [
            "git checkout -b new-branch",
            "git checkout -b feature/new-feature",
            "git checkout -b bugfix/issue-123 origin/main",
        ]

        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""  # No uncommitted changes
            mock_run.return_value = mock_result

            for command in test_cases:
                should_block, reason = check_git_checkout_command(command)
                assert should_block is False, f"Command '{command}' should be allowed"
                assert reason is None

    def test_checkout_help_allowed(self):
        """Checkout with --help flag should be allowed."""
        test_cases = [
            "git checkout --help",
            "git checkout -h",
        ]

        for command in test_cases:
            should_block, reason = check_git_checkout_command(command)
            assert should_block is False, f"Command '{command}' should be allowed"
            assert reason is None

    def test_checkout_force_always_blocked(self):
        """Checkout with -f or --force should always be blocked."""
        test_cases = [
            "git checkout -f main",
            "git checkout --force main",
            "git checkout -f",
            "git checkout --force",
        ]

        for command in test_cases:
            should_block, reason = check_git_checkout_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "DANGEROUS" in reason or "FORCES" in reason

    def test_checkout_dot_always_blocked(self):
        """'git checkout .' should always be blocked."""
        test_cases = [
            "git checkout .",
            "git checkout . ",
        ]

        for command in test_cases:
            should_block, reason = check_git_checkout_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "DISCARD ALL" in reason

    def test_checkout_dot_with_extra_spaces_not_matched(self):
        """Commands with extra spaces between words don't match the pattern."""
        # The hook checks command.strip().startswith("git checkout")
        # So "git   checkout   ." after strip becomes "git   checkout   ." which doesn't start with "git checkout"
        command = "git   checkout   ."
        should_block, reason = check_git_checkout_command(command)
        # This won't be caught because it doesn't match the pattern
        assert should_block is False

    def test_checkout_double_dash_dot_blocked(self):
        """'git checkout -- .' and similar patterns should be blocked."""
        # Commands with branch/ref before -- are caught by dangerous patterns
        dangerous_pattern_commands = [
            "git checkout main -- .",
            "git checkout HEAD -- .",
        ]

        for command in dangerous_pattern_commands:
            # These dangerous patterns are always blocked by regex
            should_block, reason = check_git_checkout_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert reason is not None
            assert "DISCARD ALL" in reason or "overwrite" in reason.lower()

        # "git checkout -- ." doesn't match dangerous patterns but is caught
        # by uncommitted changes check, so we need to test both scenarios
        with patch('subprocess.run') as mock_run:
            # Scenario 1: No uncommitted changes - will be allowed
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            should_block, reason = check_git_checkout_command("git checkout -- .")
            # Without uncommitted changes, this passes through
            assert should_block is False

        # Scenario 2: With uncommitted changes - will be blocked
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = " M file.py\n"
            mock_run.return_value = mock_result

            should_block, reason = check_git_checkout_command("git checkout -- .")
            assert should_block is True
            assert "uncommitted" in reason.lower() or "DISCARD" in reason

    def test_checkout_with_uncommitted_changes_warns(self):
        """Checkout with uncommitted changes should show warning."""
        command = "git checkout main"

        with patch('subprocess.run') as mock_run:
            # Mock git status to show uncommitted changes
            mock_result = MagicMock()
            mock_result.stdout = " M file1.py\n M file2.py\n?? newfile.py\n"
            mock_run.return_value = mock_result

            should_block, reason = check_git_checkout_command(command)

            assert should_block is True
            assert reason is not None
            assert "WARNING" in reason
            assert "uncommitted change" in reason.lower()
            assert "file1.py" in reason or "3" in reason  # Either file name or count

    def test_checkout_without_uncommitted_changes_allowed(self):
        """Checkout without uncommitted changes should be allowed."""
        command = "git checkout main"

        with patch('subprocess.run') as mock_run:
            # Mock git status to show no changes
            mock_result = MagicMock()
            mock_result.stdout = ""  # Empty means no changes
            mock_run.return_value = mock_result

            should_block, reason = check_git_checkout_command(command)

            assert should_block is False
            assert reason is None

    def test_checkout_subprocess_error_handling(self):
        """Test that subprocess errors are handled gracefully."""
        command = "git checkout main"

        with patch('subprocess.run') as mock_run:
            # Simulate subprocess error
            mock_run.side_effect = Exception("Git command failed")

            should_block, reason = check_git_checkout_command(command)

            assert should_block is True
            assert reason is not None
            assert "Could not verify" in reason

    def test_checkout_with_file_path_and_uncommitted_changes(self):
        """Checkout with file path and uncommitted changes should warn."""
        command = "git checkout main -- file.py"

        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = " M file.py\n"
            mock_run.return_value = mock_result

            should_block, reason = check_git_checkout_command(command)

            assert should_block is True
            assert reason is not None
            assert "uncommitted" in reason.lower()

    def test_checkout_many_uncommitted_files_truncated(self):
        """Test that many uncommitted files are truncated in warning."""
        command = "git checkout main"

        with patch('subprocess.run') as mock_run:
            # Mock many uncommitted changes
            mock_result = MagicMock()
            changes = "\n".join([f" M file{i}.py" for i in range(15)])
            mock_result.stdout = changes
            mock_run.return_value = mock_result

            should_block, reason = check_git_checkout_command(command)

            assert should_block is True
            assert "..." in reason or "more" in reason.lower()
            # Should show first 10 and indicate more
            assert "5 more" in reason or "and 5" in reason

    def test_dangerous_patterns_regex_matching(self):
        """Test that regex patterns correctly identify dangerous commands."""
        # These should all be blocked by dangerous patterns
        test_cases = [
            ("git checkout --force", "FORCES"),
            ("git checkout -f", "FORCES"),
            ("git checkout .", "DISCARD ALL"),
            ("git checkout main -- .", "DISCARD ALL"),
            ("git checkout HEAD -- file.py", "overwrite"),
        ]

        for command, expected_keyword in test_cases:
            should_block, reason = check_git_checkout_command(command)
            assert should_block is True, f"Command '{command}' should be blocked"
            assert expected_keyword in reason or expected_keyword.lower() in reason.lower()

    def test_safe_checkout_branch_allowed(self):
        """Test that safe branch checkouts are allowed."""
        test_cases = [
            "git checkout main",
            "git checkout feature/branch",
            "git checkout v1.0.0",
            "git checkout HEAD~1",
        ]

        with patch('subprocess.run') as mock_run:
            # Mock no uncommitted changes
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            for command in test_cases:
                should_block, reason = check_git_checkout_command(command)
                assert should_block is False, f"Safe command '{command}' should be allowed"
                assert reason is None

    def test_checkout_file_restore_warning(self):
        """Test that file restore operations show appropriate warnings."""
        command = "git checkout HEAD -- file.py"

        # This should be blocked by the dangerous pattern
        should_block, reason = check_git_checkout_command(command)

        assert should_block is True
        assert "overwrite" in reason.lower() or "version from another" in reason.lower()

    def test_multiple_subprocess_calls(self):
        """Test that multiple subprocess calls are made correctly."""
        command = "git checkout main"

        with patch('subprocess.run') as mock_run:
            # Mock git status showing changes
            mock_result = MagicMock()
            mock_result.stdout = " M file.py\n"
            mock_run.return_value = mock_result

            check_git_checkout_command(command)

            # Verify that subprocess.run was called
            assert mock_run.called
            # Should call git status and potentially git diff commands
            assert mock_run.call_count >= 1


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestHookIntegration:
    """Integration tests that verify hooks work together correctly."""

    def test_commit_and_add_hooks_independent(self, temp_git_dir):
        """Test that commit and add hooks don't interfere with each other."""
        # First, test git add
        add_command = "git add src/"
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "src/file.py\n"
            mock_run.return_value = mock_result

            should_block, _ = check_git_add_command(add_command)
            assert should_block is True

        # Then test git commit - should not be affected by add flag
        commit_command = "git commit -m 'test'"
        should_block, _ = check_git_commit_command(commit_command)
        assert should_block is True

        # Each should have their own flag files
        # Compute hash-based flag file name for git add
        safe_name = hashlib.sha256("src".encode()).hexdigest()[:16]
        add_flag = temp_git_dir / f".claude_git_add_dir_{safe_name}.flag"
        commit_flag = temp_git_dir / ".claude_git_commit_warning.flag"
        assert add_flag.exists()
        assert commit_flag.exists()

    def test_all_hooks_handle_empty_commands(self):
        """Test that all hooks handle empty commands gracefully."""
        empty_commands = ["", "   ", "\n", "\t"]

        for command in empty_commands:
            # Should not crash or throw exceptions
            should_block, reason = check_git_commit_command(command)
            assert should_block is False

            should_block, reason = check_git_add_command(command)
            assert should_block is False

            should_block, reason = check_git_checkout_command(command)
            assert should_block is False

    def test_all_hooks_preserve_working_directory(self, temp_git_dir):
        """Test that hooks don't change the working directory."""
        original_cwd = os.getcwd()

        # Run various hook checks
        check_git_commit_command("git commit -m 'test'")
        assert os.getcwd() == original_cwd

        with patch('subprocess.run'):
            check_git_add_command("git add src/")
            assert os.getcwd() == original_cwd

            check_git_checkout_command("git checkout main")
            assert os.getcwd() == original_cwd


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
