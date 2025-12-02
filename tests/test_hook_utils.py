#!/usr/bin/env python3
"""Tests for hook_utils module."""

import json
import sys
from io import StringIO

import pytest

sys.path.insert(0, str(__file__).rsplit('/', 2)[0] + '/hooks')

from hook_utils import load_and_validate_input, approve, block


class TestLoadAndValidateInput:
    """Tests for load_and_validate_input function."""

    def test_valid_json_dict(self, monkeypatch):
        """Valid JSON dict input is returned."""
        input_data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        monkeypatch.setattr('sys.stdin', StringIO(json.dumps(input_data)))

        result = load_and_validate_input()
        assert result == input_data

    def test_invalid_json_blocks(self, monkeypatch):
        """Invalid JSON causes block and exit."""
        monkeypatch.setattr('sys.stdin', StringIO("not valid json"))

        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            load_and_validate_input()

        assert exc_info.value.code == 1
        result = json.loads(output.getvalue())
        assert result["decision"] == "block"
        assert "Invalid JSON" in result["reason"]

    def test_json_array_blocks(self, monkeypatch):
        """JSON array (not dict) causes block."""
        monkeypatch.setattr('sys.stdin', StringIO('["array", "not", "dict"]'))

        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            load_and_validate_input()

        assert exc_info.value.code == 1
        result = json.loads(output.getvalue())
        assert result["decision"] == "block"
        assert "expected object" in result["reason"]

    def test_json_string_blocks(self, monkeypatch):
        """JSON string (not dict) causes block."""
        monkeypatch.setattr('sys.stdin', StringIO('"just a string"'))

        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            load_and_validate_input()

        assert exc_info.value.code == 1

    def test_empty_dict_allowed(self, monkeypatch):
        """Empty dict is valid input."""
        monkeypatch.setattr('sys.stdin', StringIO('{}'))

        result = load_and_validate_input()
        assert result == {}

    def test_nested_dict(self, monkeypatch):
        """Nested dict structure is preserved."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {
                "file_path": "/some/path",
                "nested": {"a": 1, "b": [1, 2, 3]}
            }
        }
        monkeypatch.setattr('sys.stdin', StringIO(json.dumps(input_data)))

        result = load_and_validate_input()
        assert result == input_data


class TestApprove:
    """Tests for approve function."""

    def test_approve_outputs_json(self, monkeypatch):
        """approve() outputs correct JSON and exits with 0."""
        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            approve()

        assert exc_info.value.code == 0
        result = json.loads(output.getvalue())
        assert result == {"decision": "approve"}


class TestBlock:
    """Tests for block function."""

    def test_block_outputs_json_with_reason(self, monkeypatch):
        """block() outputs correct JSON with reason and exits with 0."""
        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit) as exc_info:
            block("Test reason")

        assert exc_info.value.code == 0
        result = json.loads(output.getvalue())
        assert result["decision"] == "block"
        assert result["reason"] == "Test reason"

    def test_block_preserves_unicode(self, monkeypatch):
        """block() preserves unicode characters."""
        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        with pytest.raises(SystemExit):
            block("Unicode: 日本語 emoji: 🚀")

        result = json.loads(output.getvalue())
        assert "日本語" in result["reason"]
        assert "🚀" in result["reason"]

    def test_block_handles_multiline_reason(self, monkeypatch):
        """block() handles multiline reasons."""
        output = StringIO()
        monkeypatch.setattr('sys.stdout', output)

        reason = """Line 1
Line 2
Line 3"""

        with pytest.raises(SystemExit):
            block(reason)

        result = json.loads(output.getvalue())
        assert "Line 1" in result["reason"]
        assert "Line 3" in result["reason"]
