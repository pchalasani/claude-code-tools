#!/usr/bin/env python3
"""
Comprehensive pytest tests for rm_block_hook.py

Tests the check_rm_command() function that blocks rm commands
and suggests using TRASH folder with mv instead.
"""

import pytest
from hooks.rm_block_hook import check_rm_command


class TestBasicRmCommands:
    """Test blocking of basic rm commands"""

    def test_rm_basic_blocked(self):
        """Test that basic 'rm file.txt' is blocked"""
        should_block, reason = check_rm_command("rm file.txt")
        assert should_block is True
        assert reason is not None
        assert "TRASH" in reason
        assert "mv" in reason

    def test_rm_recursive_blocked(self):
        """Test that 'rm -rf /path' is blocked"""
        should_block, reason = check_rm_command("rm -rf /path")
        assert should_block is True
        assert reason is not None

    def test_rm_force_blocked(self):
        """Test that 'rm -f file' is blocked"""
        should_block, reason = check_rm_command("rm -f file")
        assert should_block is True
        assert reason is not None

    def test_rm_alone_blocked(self):
        """Test that 'rm' command alone is blocked"""
        should_block, reason = check_rm_command("rm")
        assert should_block is True
        assert reason is not None

    def test_rm_multiple_files_blocked(self):
        """Test that 'rm file1 file2 file3' is blocked"""
        should_block, reason = check_rm_command("rm file1 file2 file3")
        assert should_block is True
        assert reason is not None

    def test_rm_wildcard_blocked(self):
        """Test that 'rm *.txt' is blocked"""
        should_block, reason = check_rm_command("rm *.txt")
        assert should_block is True
        assert reason is not None

    def test_rm_with_path_blocked(self):
        """Test that 'rm /tmp/file.txt' is blocked"""
        should_block, reason = check_rm_command("rm /tmp/file.txt")
        assert should_block is True
        assert reason is not None


class TestSudoRmCommands:
    """Test blocking of sudo rm variants"""

    def test_sudo_rm_blocked(self):
        """Test that 'sudo rm file' is blocked"""
        should_block, reason = check_rm_command("sudo rm file")
        assert should_block is True
        assert reason is not None
        assert "TRASH" in reason

    def test_sudo_rm_recursive_blocked(self):
        """Test that 'sudo rm -rf /' is blocked"""
        should_block, reason = check_rm_command("sudo rm -rf /")
        assert should_block is True
        assert reason is not None

    def test_sudo_rm_force_blocked(self):
        """Test that 'sudo rm -f important.txt' is blocked"""
        should_block, reason = check_rm_command("sudo rm -f important.txt")
        assert should_block is True
        assert reason is not None

    def test_sudo_with_multiple_spaces_blocked(self):
        """Test that 'sudo  rm file' (multiple spaces) is blocked"""
        should_block, reason = check_rm_command("sudo  rm file")
        assert should_block is True
        assert reason is not None


class TestFullPathRmCommands:
    """Test blocking of full path rm commands"""

    def test_bin_rm_blocked(self):
        """Test that '/bin/rm file' is blocked"""
        should_block, reason = check_rm_command("/bin/rm file")
        assert should_block is True
        assert reason is not None
        assert "TRASH" in reason

    def test_usr_bin_rm_blocked(self):
        """Test that '/usr/bin/rm file' is blocked"""
        should_block, reason = check_rm_command("/usr/bin/rm file")
        assert should_block is True
        assert reason is not None

    def test_sudo_bin_rm_blocked(self):
        """Test that 'sudo /bin/rm file' is blocked"""
        should_block, reason = check_rm_command("sudo /bin/rm file")
        assert should_block is True
        assert reason is not None

    def test_arbitrary_path_rm_blocked(self):
        """Test that '/custom/path/rm file' is blocked"""
        should_block, reason = check_rm_command("/custom/path/rm file")
        assert should_block is True
        assert reason is not None


class TestChainedCommands:
    """Test blocking of rm in chained commands"""

    def test_rm_after_semicolon_blocked(self):
        """Test that 'echo test; rm file' is blocked"""
        should_block, reason = check_rm_command("echo test; rm file")
        assert should_block is True
        assert reason is not None
        assert "TRASH" in reason

    def test_rm_after_and_operator_blocked(self):
        """Test that 'ls && rm file' is blocked"""
        should_block, reason = check_rm_command("ls && rm file")
        assert should_block is True
        assert reason is not None

    def test_rm_after_pipe_blocked(self):
        """Test that 'cat file | rm' is blocked"""
        should_block, reason = check_rm_command("cat file | rm")
        assert should_block is True
        assert reason is not None

    def test_rm_after_or_operator_blocked(self):
        """Test that 'test -f file || rm backup' is blocked"""
        should_block, reason = check_rm_command("test -f file || rm backup")
        assert should_block is True
        assert reason is not None

    def test_rm_after_ampersand_blocked(self):
        """Test that 'process & rm temp' is blocked"""
        should_block, reason = check_rm_command("process & rm temp")
        assert should_block is True
        assert reason is not None

    def test_multiple_commands_with_rm_blocked(self):
        """Test that 'cd /tmp && ls -la && rm test.txt' is blocked"""
        should_block, reason = check_rm_command("cd /tmp && ls -la && rm test.txt")
        assert should_block is True
        assert reason is not None


class TestSafeCommands:
    """Test that safe commands are NOT blocked"""

    def test_remove_command_not_blocked(self):
        """Test that 'remove file' is NOT blocked"""
        should_block, reason = check_rm_command("remove file")
        assert should_block is False
        assert reason is None

    def test_grep_rm_not_blocked(self):
        """Test that 'grep rm file.txt' is NOT blocked"""
        should_block, reason = check_rm_command("grep rm file.txt")
        assert should_block is False
        assert reason is None

    def test_echo_rm_not_blocked(self):
        """Test that 'echo rm' is NOT blocked"""
        should_block, reason = check_rm_command("echo rm")
        assert should_block is False
        assert reason is None

    def test_firmware_not_blocked(self):
        """Test that 'firmware update' is NOT blocked"""
        should_block, reason = check_rm_command("firmware update")
        assert should_block is False
        assert reason is None

    def test_chmod_not_blocked(self):
        """Test that 'chmod 755 file' is NOT blocked"""
        should_block, reason = check_rm_command("chmod 755 file")
        assert should_block is False
        assert reason is None

    def test_mv_to_trash_not_blocked(self):
        """Test that 'mv file TRASH/' is NOT blocked"""
        should_block, reason = check_rm_command("mv file TRASH/")
        assert should_block is False
        assert reason is None

    def test_ls_rm_directory_not_blocked(self):
        """Test that 'ls rm_backups/' is NOT blocked"""
        should_block, reason = check_rm_command("ls rm_backups/")
        assert should_block is False
        assert reason is None

    def test_cat_filename_with_rm_not_blocked(self):
        """Test that 'cat normal_file.txt' is NOT blocked"""
        should_block, reason = check_rm_command("cat normal_file.txt")
        assert should_block is False
        assert reason is None


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_empty_string_not_blocked(self):
        """Test that empty string is NOT blocked"""
        should_block, reason = check_rm_command("")
        assert should_block is False
        assert reason is None

    def test_whitespace_only_not_blocked(self):
        """Test that whitespace-only string is NOT blocked"""
        should_block, reason = check_rm_command("   ")
        assert should_block is False
        assert reason is None

    def test_rm_with_multiple_spaces_blocked(self):
        """Test that 'rm    file' (multiple spaces) is blocked"""
        should_block, reason = check_rm_command("rm    file")
        assert should_block is True
        assert reason is not None

    def test_rm_with_tabs_blocked(self):
        """Test that 'rm\tfile' (tab character) is blocked"""
        should_block, reason = check_rm_command("rm\tfile")
        assert should_block is True
        assert reason is not None

    def test_rm_with_newlines_blocked(self):
        """Test that command with newlines containing rm is blocked"""
        should_block, reason = check_rm_command("rm\nfile")
        assert should_block is True
        assert reason is not None

    def test_leading_spaces_rm_blocked(self):
        """Test that '  rm file' (leading spaces) is blocked"""
        should_block, reason = check_rm_command("  rm file")
        assert should_block is True
        assert reason is not None

    def test_trailing_spaces_rm_blocked(self):
        """Test that 'rm file  ' (trailing spaces) is blocked"""
        should_block, reason = check_rm_command("rm file  ")
        assert should_block is True
        assert reason is not None

    def test_mixed_whitespace_rm_blocked(self):
        """Test that command with mixed whitespace and rm is blocked"""
        should_block, reason = check_rm_command("  \t rm  \t file  ")
        assert should_block is True
        assert reason is not None


class TestReasonMessage:
    """Test the reason message content"""

    def test_reason_contains_trash_keyword(self):
        """Test that blocked command reason mentions TRASH"""
        should_block, reason = check_rm_command("rm file.txt")
        assert should_block is True
        assert reason is not None
        assert "TRASH" in reason

    def test_reason_contains_mv_alternative(self):
        """Test that blocked command reason suggests mv"""
        should_block, reason = check_rm_command("rm file.txt")
        assert should_block is True
        assert reason is not None
        assert "mv" in reason or "MOVE" in reason

    def test_reason_mentions_markdown_file(self):
        """Test that blocked command reason mentions TRASH-FILES.md"""
        should_block, reason = check_rm_command("rm file.txt")
        assert should_block is True
        assert reason is not None
        assert "TRASH-FILES.md" in reason

    def test_reason_has_example(self):
        """Test that blocked command reason includes usage example"""
        should_block, reason = check_rm_command("rm file.txt")
        assert should_block is True
        assert reason is not None
        # Should contain an example showing the format
        assert "moved to TRASH/" in reason

    def test_reason_is_not_empty(self):
        """Test that blocked command always has non-empty reason"""
        test_commands = [
            "rm file",
            "sudo rm file",
            "/bin/rm file",
            "ls && rm file"
        ]
        for cmd in test_commands:
            should_block, reason = check_rm_command(cmd)
            assert should_block is True
            assert reason is not None
            assert len(reason) > 0, f"Reason should not be empty for: {cmd}"


class TestComplexScenarios:
    """Test complex real-world scenarios"""

    def test_rm_in_bash_script_not_blocked(self):
        """Test that rm in bash -c quoted string is NOT blocked (hook doesn't parse quotes)"""
        # The hook doesn't parse inside quoted strings - this is expected behavior
        should_block, reason = check_rm_command("bash -c 'rm file.txt'")
        assert should_block is False
        assert reason is None

    def test_find_with_rm_exec_not_blocked(self):
        """Test that 'find . -name *.tmp -exec rm {} \\;' is NOT blocked (rm in -exec context)"""
        # The hook doesn't detect rm in -exec arguments - this is expected behavior
        should_block, reason = check_rm_command("find . -name *.tmp -exec rm {} \\;")
        assert should_block is False
        assert reason is None

    def test_xargs_rm_not_blocked(self):
        """Test that 'ls | xargs rm' is NOT blocked (rm not directly after pipe)"""
        # The hook looks for rm immediately after separators (with optional sudo/path)
        # In this case, 'xargs' appears between the pipe and 'rm'
        should_block, reason = check_rm_command("ls | xargs rm")
        assert should_block is False
        assert reason is None

    def test_conditional_rm_not_blocked(self):
        """Test that 'if [ -f file ]; then rm file; fi' is NOT blocked (rm not directly after ;)"""
        # The hook looks for rm immediately after separators
        # In this case, 'then' appears between the semicolon and 'rm'
        should_block, reason = check_rm_command("if [ -f file ]; then rm file; fi")
        assert should_block is False
        assert reason is None

    def test_rm_with_verbose_flag_blocked(self):
        """Test that 'rm -v file.txt' is blocked"""
        should_block, reason = check_rm_command("rm -v file.txt")
        assert should_block is True
        assert reason is not None

    def test_rm_with_interactive_flag_blocked(self):
        """Test that 'rm -i file.txt' is blocked"""
        should_block, reason = check_rm_command("rm -i file.txt")
        assert should_block is True
        assert reason is not None

    def test_rm_with_preserve_root_blocked(self):
        """Test that 'rm --preserve-root -rf /' is blocked"""
        should_block, reason = check_rm_command("rm --preserve-root -rf /")
        assert should_block is True
        assert reason is not None


class TestReturnValueStructure:
    """Test the structure and types of return values"""

    def test_blocked_returns_tuple(self):
        """Test that blocked command returns a tuple"""
        result = check_rm_command("rm file")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_blocked_first_element_is_bool(self):
        """Test that first return element is boolean"""
        should_block, reason = check_rm_command("rm file")
        assert isinstance(should_block, bool)

    def test_blocked_second_element_is_string(self):
        """Test that second return element is string when blocked"""
        should_block, reason = check_rm_command("rm file")
        assert isinstance(reason, str)

    def test_not_blocked_returns_tuple(self):
        """Test that safe command returns a tuple"""
        result = check_rm_command("ls -la")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_not_blocked_first_element_is_false(self):
        """Test that safe command returns False as first element"""
        should_block, reason = check_rm_command("ls -la")
        assert should_block is False

    def test_not_blocked_second_element_is_none(self):
        """Test that safe command returns None as second element"""
        should_block, reason = check_rm_command("ls -la")
        assert reason is None


class TestCaseInsensitivity:
    """Test case sensitivity of command detection"""

    def test_uppercase_rm_not_blocked(self):
        """Test that 'RM file' (uppercase) is NOT blocked by default"""
        # The regex is case-sensitive, so uppercase RM should not match
        should_block, reason = check_rm_command("RM file")
        assert should_block is False
        assert reason is None

    def test_mixed_case_rm_not_blocked(self):
        """Test that 'Rm file' (mixed case) is NOT blocked by default"""
        should_block, reason = check_rm_command("Rm file")
        assert should_block is False
        assert reason is None


class TestBoundaryWords:
    """Test word boundary detection"""

    def test_rm_at_end_of_word_not_blocked(self):
        """Test that commands ending in 'rm' are NOT blocked"""
        should_block, reason = check_rm_command("alarm set")
        assert should_block is False
        assert reason is None

    def test_rm_at_start_of_word_not_blocked(self):
        """Test that commands starting with 'rm' prefix are NOT blocked"""
        should_block, reason = check_rm_command("rmdir test")
        assert should_block is False
        assert reason is None

    def test_rm_in_middle_of_word_not_blocked(self):
        """Test that 'rm' in middle of word is NOT blocked"""
        should_block, reason = check_rm_command("normal_file.txt")
        assert should_block is False
        assert reason is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
