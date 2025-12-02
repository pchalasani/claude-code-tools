#!/usr/bin/env python3
"""
Comprehensive pytest tests for file-related hooks:
- file_size_conditional_hook.py
- pretask_subtask_flag.py
- posttask_subtask_flag.py
- grep_block_hook.py
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch

import pytest


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def subtask_flag():
    """Create and cleanup the subtask flag file."""
    flag_file = Path('.claude_in_subtask.flag')
    yield flag_file
    # Cleanup after test
    if flag_file.exists():
        flag_file.unlink()


@pytest.fixture
def hooks_dir():
    """Get the absolute path to the hooks directory."""
    test_dir = Path(__file__).parent
    hooks_dir = test_dir.parent / 'hooks'
    assert hooks_dir.exists(), f"Hooks directory not found: {hooks_dir}"
    return hooks_dir


@pytest.fixture
def sample_text_file(temp_dir):
    """Create a sample text file with a known number of lines."""
    file_path = temp_dir / "sample.txt"
    content = "\n".join([f"Line {i}" for i in range(1, 101)])  # 100 lines
    file_path.write_text(content)
    return file_path


@pytest.fixture
def large_text_file(temp_dir):
    """Create a large text file with >500 lines."""
    file_path = temp_dir / "large.txt"
    content = "\n".join([f"Line {i}" for i in range(1, 601)])  # 600 lines
    file_path.write_text(content)
    return file_path


@pytest.fixture
def huge_text_file(temp_dir):
    """Create a huge text file with >10,000 lines."""
    file_path = temp_dir / "huge.txt"
    content = "\n".join([f"Line {i}" for i in range(1, 10_101)])  # 10,100 lines
    file_path.write_text(content)
    return file_path


@pytest.fixture
def binary_file_null_bytes(temp_dir):
    """Create a binary file with null bytes."""
    file_path = temp_dir / "binary_null.bin"
    file_path.write_bytes(b'\x00\x01\x02\x03\x04\x05\x06\x07')
    return file_path


@pytest.fixture
def binary_file_non_utf8(temp_dir):
    """Create a binary file with non-UTF-8 bytes."""
    file_path = temp_dir / "binary_non_utf8.bin"
    # Invalid UTF-8 sequence
    file_path.write_bytes(b'\xff\xfe\xfd\xfc\xfb')
    return file_path


@pytest.fixture
def empty_file(temp_dir):
    """Create an empty file."""
    file_path = temp_dir / "empty.txt"
    file_path.write_text("")
    return file_path


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def run_hook(hook_script: Path, stdin_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a hook script with JSON input via stdin and return the parsed output.

    Args:
        hook_script: Path to the hook script
        stdin_data: Dictionary to pass as JSON via stdin

    Returns:
        Parsed JSON output from the hook
    """
    result = subprocess.run(
        [str(hook_script)],
        input=json.dumps(stdin_data).encode(),
        capture_output=True,
        timeout=5
    )

    # Parse the JSON output
    output = json.loads(result.stdout.decode())
    return output


# ============================================================================
# TEST: file_size_conditional_hook.py
# ============================================================================

class TestFileSizeConditionalHook:
    """Tests for file_size_conditional_hook.py"""

    @pytest.fixture
    def hook_script(self, hooks_dir):
        """Get the file_size_conditional_hook.py script."""
        return hooks_dir / 'file_size_conditional_hook.py'

    def test_binary_file_detection_null_bytes(self, hook_script, binary_file_null_bytes, subtask_flag):
        """Binary files with null bytes should always be approved."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(binary_file_null_bytes)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_binary_file_detection_non_utf8(self, hook_script, binary_file_non_utf8, subtask_flag):
        """Binary files with non-UTF-8 bytes should always be approved."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(binary_file_non_utf8)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_empty_file_handling(self, hook_script, empty_file, subtask_flag):
        """Empty files should be treated as text and approved."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(empty_file)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_line_counting_accuracy(self, hook_script, sample_text_file, subtask_flag):
        """Verify line counting is accurate for text files."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(sample_text_file)
            }
        }

        # Sample file has 100 lines, which is < 500, so should be approved
        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_main_agent_small_file_approved(self, hook_script, sample_text_file, subtask_flag):
        """Main agent should approve files <= 500 lines."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(sample_text_file)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_main_agent_large_file_blocked(self, hook_script, large_text_file, subtask_flag):
        """Main agent should block files > 500 lines."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(large_text_file)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "block"
        assert "600 lines" in result["reason"]
        assert "SUB-AGENT" in result["reason"] or "Task tool" in result["reason"]

    def test_subtask_mode_medium_file_approved(self, hook_script, large_text_file, subtask_flag):
        """Subtask should approve files > 500 but <= 10,000 lines."""
        # Create the subtask flag
        subtask_flag.write_text('1')

        stdin_data = {
            "tool_input": {
                "file_path": str(large_text_file)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_subtask_mode_huge_file_blocked(self, hook_script, huge_text_file, subtask_flag):
        """Subtask should block files > 10,000 lines with gemini-cli suggestion."""
        # Create the subtask flag
        subtask_flag.write_text('1')

        stdin_data = {
            "tool_input": {
                "file_path": str(huge_text_file)
            }
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "block"
        assert "10100 lines" in result["reason"] or "10,100 lines" in result["reason"]
        assert "gemini" in result["reason"].lower() or "Gemini" in result["reason"]

    def test_offset_limit_calculations(self, hook_script, sample_text_file, subtask_flag):
        """Test that offset and limit are properly handled."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(sample_text_file),
                "offset": 10,
                "limit": 50
            }
        }

        # With offset=10 and limit=50, effective lines = 50
        # This is < 500, so should be approved
        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_missing_file_handling(self, hook_script, temp_dir, subtask_flag):
        """Test behavior when file doesn't exist."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        non_existent = temp_dir / "does_not_exist.txt"

        stdin_data = {
            "tool_input": {
                "file_path": str(non_existent)
            }
        }

        # If file doesn't exist, hook should approve (doesn't check it)
        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"

    def test_no_file_path_provided(self, hook_script, subtask_flag):
        """Test behavior when no file_path is provided."""
        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {}
        }

        result = run_hook(hook_script, stdin_data)
        assert result["decision"] == "approve"


# ============================================================================
# TEST: pretask_subtask_flag.py
# ============================================================================

class TestPretaskSubtaskFlag:
    """Tests for pretask_subtask_flag.py"""

    @pytest.fixture
    def hook_script(self, hooks_dir):
        """Get the pretask_subtask_flag.py script."""
        return hooks_dir / 'pretask_subtask_flag.py'

    def test_flag_file_created(self, hook_script, subtask_flag):
        """Test that the flag file is created."""
        # Ensure flag doesn't exist before test
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        # Verify the flag was created
        assert subtask_flag.exists()
        assert result["decision"] == "approve"

    def test_flag_file_contains_one(self, hook_script, subtask_flag):
        """Test that the flag file contains '1'."""
        # Ensure flag doesn't exist before test
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        # Verify the flag content
        assert subtask_flag.exists()
        content = subtask_flag.read_text()
        assert content == '1'
        assert result["decision"] == "approve"

    def test_returns_approve_decision(self, hook_script, subtask_flag):
        """Test that hook returns approve decision."""
        # Ensure flag doesn't exist before test
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        assert result["decision"] == "approve"
        assert "reason" not in result  # Should only have decision


# ============================================================================
# TEST: posttask_subtask_flag.py
# ============================================================================

class TestPosttaskSubtaskFlag:
    """Tests for posttask_subtask_flag.py"""

    @pytest.fixture
    def hook_script(self, hooks_dir):
        """Get the posttask_subtask_flag.py script."""
        return hooks_dir / 'posttask_subtask_flag.py'

    def test_flag_file_removed_when_exists(self, hook_script, subtask_flag):
        """Test that the flag file is removed when it exists."""
        # Create the flag first
        subtask_flag.write_text('1')
        assert subtask_flag.exists()

        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        # Verify the flag was removed
        assert not subtask_flag.exists()
        assert result["decision"] == "approve"

    def test_no_error_when_flag_doesnt_exist(self, hook_script, subtask_flag):
        """Test that no error occurs when flag doesn't exist."""
        # Ensure flag doesn't exist
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        # Should succeed without error
        assert result["decision"] == "approve"
        assert not subtask_flag.exists()

    def test_returns_approve_decision(self, hook_script, subtask_flag):
        """Test that hook returns approve decision."""
        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        assert result["decision"] == "approve"
        assert "reason" not in result  # Should only have decision


# ============================================================================
# TEST: grep_block_hook.py
# ============================================================================

class TestGrepBlockHook:
    """Tests for grep_block_hook.py"""

    @pytest.fixture
    def hook_script(self, hooks_dir):
        """Get the grep_block_hook.py script."""
        return hooks_dir / 'grep_block_hook.py'

    def test_always_blocks(self, hook_script):
        """Test that the hook always blocks."""
        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        assert result["decision"] == "block"

    def test_message_mentions_ripgrep(self, hook_script):
        """Test that the block message mentions ripgrep (rg)."""
        stdin_data = {}
        result = run_hook(hook_script, stdin_data)

        assert result["decision"] == "block"
        assert "reason" in result
        assert "rg" in result["reason"] or "ripgrep" in result["reason"].lower()

    def test_blocks_regardless_of_input(self, hook_script):
        """Test that hook blocks even with various inputs."""
        test_inputs = [
            {},
            {"tool_input": {}},
            {"tool_input": {"command": "grep 'pattern' file.txt"}},
            {"some_other_field": "value"}
        ]

        for stdin_data in test_inputs:
            result = run_hook(hook_script, stdin_data)
            assert result["decision"] == "block"
            assert "rg" in result["reason"] or "ripgrep" in result["reason"].lower()


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestHooksIntegration:
    """Integration tests for hook interactions."""

    def test_subtask_flag_lifecycle(self, hooks_dir, subtask_flag):
        """Test the complete lifecycle of subtask flag creation and removal."""
        pretask_hook = hooks_dir / 'pretask_subtask_flag.py'
        posttask_hook = hooks_dir / 'posttask_subtask_flag.py'

        # Ensure flag doesn't exist initially
        if subtask_flag.exists():
            subtask_flag.unlink()
        assert not subtask_flag.exists()

        # Run pretask hook - should create flag
        result1 = run_hook(pretask_hook, {})
        assert result1["decision"] == "approve"
        assert subtask_flag.exists()
        assert subtask_flag.read_text() == '1'

        # Run posttask hook - should remove flag
        result2 = run_hook(posttask_hook, {})
        assert result2["decision"] == "approve"
        assert not subtask_flag.exists()

    def test_file_size_hook_behavior_with_flag_transitions(
        self, hooks_dir, large_text_file, subtask_flag
    ):
        """Test file_size_conditional_hook behavior during flag transitions."""
        pretask_hook = hooks_dir / 'pretask_subtask_flag.py'
        posttask_hook = hooks_dir / 'posttask_subtask_flag.py'
        file_size_hook = hooks_dir / 'file_size_conditional_hook.py'

        # Ensure flag doesn't exist initially
        if subtask_flag.exists():
            subtask_flag.unlink()

        stdin_data = {
            "tool_input": {
                "file_path": str(large_text_file)
            }
        }

        # Test 1: Main agent mode - should block large file
        result1 = run_hook(file_size_hook, stdin_data)
        assert result1["decision"] == "block"

        # Test 2: Enter subtask - create flag
        run_hook(pretask_hook, {})
        assert subtask_flag.exists()

        # Test 3: Subtask mode - should approve same large file
        result2 = run_hook(file_size_hook, stdin_data)
        assert result2["decision"] == "approve"

        # Test 4: Exit subtask - remove flag
        run_hook(posttask_hook, {})
        assert not subtask_flag.exists()

        # Test 5: Back to main agent - should block again
        result3 = run_hook(file_size_hook, stdin_data)
        assert result3["decision"] == "block"


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

class TestEdgeCases:
    """Edge case tests for all hooks."""

    def test_malformed_json_handling(self, hooks_dir):
        """Test hook behavior with malformed JSON input."""
        file_size_hook = hooks_dir / 'file_size_conditional_hook.py'

        # Test with completely invalid JSON
        result = subprocess.run(
            [str(file_size_hook)],
            input=b'not valid json',
            capture_output=True,
            timeout=5
        )

        # Hook should fail gracefully (non-zero exit code or stderr)
        # Either it exits with error or produces error output
        assert result.returncode != 0 or len(result.stderr) > 0

    def test_concurrent_flag_operations(self, hooks_dir, subtask_flag):
        """Test that flag operations are safe for concurrent access."""
        pretask_hook = hooks_dir / 'pretask_subtask_flag.py'
        posttask_hook = hooks_dir / 'posttask_subtask_flag.py'

        # Ensure flag doesn't exist
        if subtask_flag.exists():
            subtask_flag.unlink()

        # Create flag multiple times - should not error
        for _ in range(3):
            result = run_hook(pretask_hook, {})
            assert result["decision"] == "approve"
            assert subtask_flag.exists()

        # Remove flag multiple times - should not error
        for _ in range(3):
            result = run_hook(posttask_hook, {})
            assert result["decision"] == "approve"

    def test_very_long_file_path(self, hooks_dir, temp_dir, subtask_flag):
        """Test file_size_conditional_hook with very long file paths."""
        file_size_hook = hooks_dir / 'file_size_conditional_hook.py'

        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        # Create a deeply nested directory structure
        deep_dir = temp_dir
        for i in range(10):
            deep_dir = deep_dir / f"subdir_{i}"
        deep_dir.mkdir(parents=True)

        # Create a file in the deep directory
        file_path = deep_dir / "test.txt"
        file_path.write_text("test content\n")

        stdin_data = {
            "tool_input": {
                "file_path": str(file_path)
            }
        }

        result = run_hook(file_size_hook, stdin_data)
        assert result["decision"] == "approve"

    def test_unicode_content_handling(self, hooks_dir, temp_dir, subtask_flag):
        """Test file_size_conditional_hook with Unicode content."""
        file_size_hook = hooks_dir / 'file_size_conditional_hook.py'

        # Ensure we're in main agent mode
        if subtask_flag.exists():
            subtask_flag.unlink()

        # Create a file with various Unicode characters
        file_path = temp_dir / "unicode.txt"
        content = "\n".join([
            "English text",
            "中文文本",
            "Русский текст",
            "العربية",
            "🚀 Emojis 💻",
            "Math: ∑∫∂√"
        ])
        file_path.write_text(content, encoding='utf-8')

        stdin_data = {
            "tool_input": {
                "file_path": str(file_path)
            }
        }

        result = run_hook(file_size_hook, stdin_data)
        assert result["decision"] == "approve"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
