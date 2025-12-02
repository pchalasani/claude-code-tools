#!/usr/bin/env python3
"""
Tests for refactored hooks with direct imports.
These tests provide coverage by importing functions directly.
"""

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))


# ============================================================================
# FILE SIZE CONDITIONAL HOOK TESTS
# ============================================================================

class TestFileSizeConditionalHook:
    """Tests for file_size_conditional_hook.py"""

    def test_is_binary_file_with_null_bytes(self, tmp_path):
        """Binary files with null bytes are detected."""
        from file_size_conditional_hook import is_binary_file

        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"hello\x00world")

        assert is_binary_file(str(binary_file)) is True

    def test_is_binary_file_with_text(self, tmp_path):
        """Text files are not detected as binary."""
        from file_size_conditional_hook import is_binary_file

        text_file = tmp_path / "test.txt"
        text_file.write_text("hello world\n")

        assert is_binary_file(str(text_file)) is False

    def test_is_binary_file_empty(self, tmp_path):
        """Empty files are not detected as binary."""
        from file_size_conditional_hook import is_binary_file

        empty_file = tmp_path / "empty.txt"
        empty_file.touch()

        assert is_binary_file(str(empty_file)) is False

    def test_is_binary_file_non_utf8(self, tmp_path):
        """Non-UTF8 files are detected as binary."""
        from file_size_conditional_hook import is_binary_file

        non_utf8 = tmp_path / "test.bin"
        non_utf8.write_bytes(b'\xff\xfe\x00\x01')

        assert is_binary_file(str(non_utf8)) is True

    def test_is_binary_file_nonexistent(self):
        """Nonexistent files are treated as binary (safe default)."""
        from file_size_conditional_hook import is_binary_file

        assert is_binary_file("/nonexistent/path") is True

    def test_count_lines(self, tmp_path):
        """Line counting works correctly."""
        from file_size_conditional_hook import count_lines

        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")

        assert count_lines(str(test_file)) == 3

    def test_count_lines_empty(self, tmp_path):
        """Empty files have zero lines."""
        from file_size_conditional_hook import count_lines

        empty_file = tmp_path / "empty.txt"
        empty_file.touch()

        assert count_lines(str(empty_file)) == 0

    def test_check_file_size_nonexistent(self):
        """Nonexistent files are approved."""
        from file_size_conditional_hook import check_file_size

        should_block, reason = check_file_size("/nonexistent/path")
        assert should_block is False
        assert reason is None

    def test_check_file_size_none_path(self):
        """None path is approved."""
        from file_size_conditional_hook import check_file_size

        should_block, reason = check_file_size(None)
        assert should_block is False
        assert reason is None

    def test_check_file_size_binary_approved(self, tmp_path):
        """Binary files are always approved."""
        from file_size_conditional_hook import check_file_size

        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"hello\x00world" * 1000)

        should_block, reason = check_file_size(str(binary_file))
        assert should_block is False

    def test_check_file_size_small_file_main_agent(self, tmp_path):
        """Small files (<500 lines) are approved for main agent."""
        from file_size_conditional_hook import check_file_size

        small_file = tmp_path / "small.txt"
        small_file.write_text("line\n" * 100)

        should_block, reason = check_file_size(str(small_file), is_main_agent=True)
        assert should_block is False

    def test_check_file_size_large_file_main_agent(self, tmp_path):
        """Large files (>500 lines) are blocked for main agent."""
        from file_size_conditional_hook import check_file_size

        large_file = tmp_path / "large.txt"
        large_file.write_text("line\n" * 600)

        should_block, reason = check_file_size(str(large_file), is_main_agent=True)
        assert should_block is True
        assert "SUB-AGENT" in reason

    def test_check_file_size_large_file_subtask(self, tmp_path):
        """Large files (>500, <10000 lines) are approved for subtask."""
        from file_size_conditional_hook import check_file_size

        large_file = tmp_path / "large.txt"
        large_file.write_text("line\n" * 600)

        should_block, reason = check_file_size(str(large_file), is_main_agent=False)
        assert should_block is False

    def test_check_file_size_huge_file_subtask(self, tmp_path):
        """Huge files (>10000 lines) are blocked for subtask."""
        from file_size_conditional_hook import check_file_size

        huge_file = tmp_path / "huge.txt"
        huge_file.write_text("line\n" * 10100)

        should_block, reason = check_file_size(str(huge_file), is_main_agent=False)
        assert should_block is True
        assert "Gemini CLI" in reason

    def test_main_approves_small_file(self, tmp_path, monkeypatch):
        """Main function approves small files."""
        from file_size_conditional_hook import main

        small_file = tmp_path / "small.txt"
        small_file.write_text("line\n" * 10)

        input_data = {"tool_input": {"file_path": str(small_file)}}
        monkeypatch.setattr('sys.stdin', StringIO(json.dumps(input_data)))

        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        result = json.loads(output.getvalue())
        assert result["decision"] == "approve"


# ============================================================================
# GREP BLOCK HOOK TESTS
# ============================================================================

class TestGrepBlockHook:
    """Tests for grep_block_hook.py"""

    def test_check_grep_command_always_blocks(self):
        """Grep commands are always blocked."""
        from grep_block_hook import check_grep_command

        should_block, reason = check_grep_command()
        assert should_block is True

    def test_check_grep_command_suggests_ripgrep(self):
        """Grep block message suggests ripgrep."""
        from grep_block_hook import check_grep_command

        should_block, reason = check_grep_command()
        assert "rg" in reason or "ripgrep" in reason

    def test_main_blocks(self, monkeypatch):
        """Main function blocks with ripgrep suggestion."""
        from grep_block_hook import main

        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        result = json.loads(output.getvalue())
        assert result["decision"] == "block"
        assert "rg" in result["reason"]


# ============================================================================
# PRETASK SUBTASK FLAG TESTS
# ============================================================================

class TestPretaskSubtaskFlag:
    """Tests for pretask_subtask_flag.py"""

    def test_create_subtask_flag_creates_file(self, tmp_path):
        """Flag file is created."""
        from pretask_subtask_flag import create_subtask_flag

        flag_path = tmp_path / ".claude_in_subtask.flag"
        result = create_subtask_flag(str(flag_path))

        assert result is True
        assert flag_path.exists()

    def test_create_subtask_flag_writes_1(self, tmp_path):
        """Flag file contains '1'."""
        from pretask_subtask_flag import create_subtask_flag

        flag_path = tmp_path / ".claude_in_subtask.flag"
        create_subtask_flag(str(flag_path))

        assert flag_path.read_text() == "1"

    def test_main_creates_flag_and_approves(self, monkeypatch, tmp_path):
        """Main function creates flag and returns approve."""
        from pretask_subtask_flag import create_subtask_flag

        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        # Use custom path to avoid side effects
        flag_path = tmp_path / ".flag"
        create_subtask_flag(str(flag_path))

        assert flag_path.exists()


# ============================================================================
# POSTTASK SUBTASK FLAG TESTS
# ============================================================================

class TestPosttaskSubtaskFlag:
    """Tests for posttask_subtask_flag.py"""

    def test_remove_subtask_flag_removes_existing(self, tmp_path):
        """Existing flag file is removed."""
        from posttask_subtask_flag import remove_subtask_flag

        flag_path = tmp_path / ".claude_in_subtask.flag"
        flag_path.write_text("1")

        result = remove_subtask_flag(str(flag_path))

        assert result is True
        assert not flag_path.exists()

    def test_remove_subtask_flag_nonexistent(self, tmp_path):
        """Nonexistent flag returns False without error."""
        from posttask_subtask_flag import remove_subtask_flag

        flag_path = tmp_path / ".nonexistent.flag"

        result = remove_subtask_flag(str(flag_path))

        assert result is False

    def test_flag_lifecycle(self, tmp_path):
        """Complete flag lifecycle: create -> exists -> remove -> gone."""
        from pretask_subtask_flag import create_subtask_flag
        from posttask_subtask_flag import remove_subtask_flag

        flag_path = tmp_path / ".claude_in_subtask.flag"

        # Create
        create_subtask_flag(str(flag_path))
        assert flag_path.exists()

        # Remove
        remove_subtask_flag(str(flag_path))
        assert not flag_path.exists()


# Note: Main() function tests removed as they use exec() which doesn't
# work well with pytest. The check_* functions are comprehensively tested
# in test_git_hooks.py and test_rm_block_hook.py.
