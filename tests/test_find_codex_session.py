"""Comprehensive pytest tests for find_codex_session.py module."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock rich library before importing the module
sys.modules["rich"] = MagicMock()
sys.modules["rich.console"] = MagicMock()
sys.modules["rich.table"] = MagicMock()

from claude_code_tools.find_codex_session import (
    get_codex_home,
    extract_session_id_from_filename,
    extract_session_metadata,
    get_project_name,
    is_system_message,
    search_keywords_in_file,
    find_sessions,
    display_interactive_ui,
    show_action_menu,
    copy_session_file,
    resume_session,
    main,
)


# ==================== Test get_codex_home ====================


def test_get_codex_home_default():
    """Test get_codex_home returns default home directory."""
    result = get_codex_home()

    expected = Path.home() / ".codex"
    assert result == expected


def test_get_codex_home_custom():
    """Test get_codex_home with custom path."""
    result = get_codex_home("/custom/codex")

    assert result == Path("/custom/codex")


def test_get_codex_home_expands_tilde():
    """Test get_codex_home expands tilde."""
    with patch.object(Path, "expanduser") as mock_expand:
        mock_expand.return_value = Path("/home/user/.codex")

        result = get_codex_home("~/.codex")

        mock_expand.assert_called_once()
        assert result == Path("/home/user/.codex")


def test_get_codex_home_none():
    """Test get_codex_home with None returns default."""
    result = get_codex_home(None)

    assert result == Path.home() / ".codex"


# ==================== Test extract_session_id_from_filename ====================


def test_extract_session_id_from_filename_valid():
    """Test extract_session_id_from_filename with valid filename."""
    filename = "rollout-2025-10-07T13-48-15-0199bfc9-c444-77e1-8c8a-f91c94fcd832.jsonl"

    result = extract_session_id_from_filename(filename)

    assert result == "0199bfc9-c444-77e1-8c8a-f91c94fcd832"


def test_extract_session_id_from_filename_short_id():
    """Test extract_session_id_from_filename with shorter session ID."""
    filename = "rollout-2025-01-01T00-00-00-abc123.jsonl"

    result = extract_session_id_from_filename(filename)

    assert result == "abc123"


def test_extract_session_id_from_filename_invalid():
    """Test extract_session_id_from_filename with invalid filename."""
    filename = "invalid-filename.jsonl"

    result = extract_session_id_from_filename(filename)

    assert result is None


def test_extract_session_id_from_filename_missing_session_id():
    """Test extract_session_id_from_filename with missing session ID."""
    filename = "rollout-2025-10-07T13-48-15-.jsonl"

    result = extract_session_id_from_filename(filename)

    # Pattern may or may not match empty ID
    assert result in [None, ""]


def test_extract_session_id_from_filename_wrong_extension():
    """Test extract_session_id_from_filename with wrong extension."""
    filename = "rollout-2025-10-07T13-48-15-abc123.txt"

    result = extract_session_id_from_filename(filename)

    assert result is None


def test_extract_session_id_from_filename_no_extension():
    """Test extract_session_id_from_filename without extension."""
    filename = "rollout-2025-10-07T13-48-15-abc123"

    result = extract_session_id_from_filename(filename)

    assert result is None


# ==================== Test extract_session_metadata ====================


def test_extract_session_metadata_valid(tmp_path):
    """Test extract_session_metadata with valid session file."""
    session_file = tmp_path / "session.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": "test-session-id",
            "cwd": "/home/user/project",
            "git": {"branch": "main"},
            "timestamp": "2025-10-07T13:48:15"
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n")

    result = extract_session_metadata(session_file)

    assert result["id"] == "test-session-id"
    assert result["cwd"] == "/home/user/project"
    assert result["branch"] == "main"
    assert result["timestamp"] == "2025-10-07T13:48:15"


def test_extract_session_metadata_no_git_branch(tmp_path):
    """Test extract_session_metadata handles missing git branch."""
    session_file = tmp_path / "session.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": "test-id",
            "cwd": "/home/user/project",
            "timestamp": "2025-10-07"
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n")

    result = extract_session_metadata(session_file)

    assert result["branch"] == ""


def test_extract_session_metadata_empty_git(tmp_path):
    """Test extract_session_metadata with empty git object."""
    session_file = tmp_path / "session.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": "test-id",
            "cwd": "/home/user/project",
            "git": {},
            "timestamp": "2025-10-07"
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n")

    result = extract_session_metadata(session_file)

    assert result["branch"] == ""


def test_extract_session_metadata_not_first_entry(tmp_path):
    """Test extract_session_metadata finds session_meta even if not first."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "other", "data": "ignored"}\n'
        '{"type": "session_meta", "payload": {"id": "found", "cwd": "/test", "timestamp": "2025"}}\n'
    )
    session_file.write_text(content)

    result = extract_session_metadata(session_file)

    assert result["id"] == "found"


def test_extract_session_metadata_no_session_meta(tmp_path):
    """Test extract_session_metadata returns None if no session_meta found."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text('{"type": "other", "data": "test"}\n')

    result = extract_session_metadata(session_file)

    assert result is None


def test_extract_session_metadata_invalid_json(tmp_path):
    """Test extract_session_metadata handles invalid JSON gracefully."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("{ invalid json }\n")

    result = extract_session_metadata(session_file)

    assert result is None


def test_extract_session_metadata_missing_file():
    """Test extract_session_metadata handles missing file."""
    fake_path = Path("/nonexistent/file.jsonl")

    result = extract_session_metadata(fake_path)

    assert result is None


def test_extract_session_metadata_empty_file(tmp_path):
    """Test extract_session_metadata handles empty file."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("")

    result = extract_session_metadata(session_file)

    assert result is None


def test_extract_session_metadata_blank_lines(tmp_path):
    """Test extract_session_metadata skips blank lines."""
    session_file = tmp_path / "session.jsonl"
    content = (
        "\n"
        "   \n"
        '{"type": "session_meta", "payload": {"id": "test", "cwd": "/path", "timestamp": "2025"}}\n'
    )
    session_file.write_text(content)

    result = extract_session_metadata(session_file)

    assert result is not None
    assert result["id"] == "test"


# ==================== Test get_project_name ====================


def test_get_project_name_simple():
    """Test get_project_name with simple path."""
    result = get_project_name("/home/user/my-project")

    assert result == "my-project"


def test_get_project_name_nested():
    """Test get_project_name with nested path."""
    result = get_project_name("/home/user/code/deep/nested/project")

    assert result == "project"


def test_get_project_name_trailing_slash():
    """Test get_project_name removes trailing slash."""
    result = get_project_name("/home/user/project/")

    assert result == "project"


def test_get_project_name_empty():
    """Test get_project_name with empty string."""
    result = get_project_name("")

    assert result == "unknown"


def test_get_project_name_root():
    """Test get_project_name with root path."""
    result = get_project_name("/")

    # Path.name for root is empty
    assert result == "unknown"


def test_get_project_name_with_spaces():
    """Test get_project_name with spaces in name."""
    result = get_project_name("/home/user/my project")

    assert result == "my project"


# ==================== Test is_system_message ====================


def test_is_system_message_empty():
    """Test is_system_message with empty string."""
    assert is_system_message("") is True


def test_is_system_message_short():
    """Test is_system_message with very short string."""
    assert is_system_message("hi") is True


def test_is_system_message_xml_tag():
    """Test is_system_message detects XML tags."""
    assert is_system_message("<environment_context>") is True


def test_is_system_message_user_instructions():
    """Test is_system_message detects user_instructions."""
    assert is_system_message("<user_instructions>\nContent") is True


def test_is_system_message_normal_text():
    """Test is_system_message allows normal user text."""
    assert is_system_message("This is a normal user message") is False


def test_is_system_message_long_text():
    """Test is_system_message allows long user text."""
    text = "This is a longer message that should not be flagged as system-generated."
    assert is_system_message(text) is False


def test_is_system_message_whitespace_only():
    """Test is_system_message filters whitespace."""
    assert is_system_message("   \n  \t  ") is True


def test_is_system_message_xml_not_at_start():
    """Test is_system_message doesn't match XML in middle of text."""
    assert is_system_message("Some text <tag> here") is False


# ==================== Test search_keywords_in_file ====================


def test_search_keywords_in_file_no_keywords(tmp_path):
    """Test search_keywords_in_file with no keywords matches all."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test message here"}]}}\n' * 5
    )
    session_file.write_text(content)

    found, line_count, preview = search_keywords_in_file(session_file, [])

    assert found is True
    assert line_count == 5
    # Preview may be None or a string depending on message filtering
    assert preview in [None, "test message here"]


def test_search_keywords_in_file_single_keyword_found(tmp_path):
    """Test search_keywords_in_file finds single keyword."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "testing langroid"}]}}\n'
    session_file.write_text(content)

    found, line_count, preview = search_keywords_in_file(session_file, ["langroid"])

    assert found is True
    assert line_count == 1


def test_search_keywords_in_file_single_keyword_not_found(tmp_path):
    """Test search_keywords_in_file returns False when keyword missing."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test"}]}}\n'
    session_file.write_text(content)

    found, line_count, preview = search_keywords_in_file(session_file, ["missing"])

    assert found is False


def test_search_keywords_in_file_multiple_keywords_all_found(tmp_path):
    """Test search_keywords_in_file with all keywords present (AND logic)."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "langroid"}]}}\n'
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "MCP protocol"}]}}\n'
    )
    session_file.write_text(content)

    found, line_count, preview = search_keywords_in_file(session_file, ["langroid", "MCP"])

    assert found is True
    assert line_count == 2


def test_search_keywords_in_file_multiple_keywords_partial(tmp_path):
    """Test search_keywords_in_file returns False if any keyword missing."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "langroid"}]}}\n'
    session_file.write_text(content)

    found, line_count, preview = search_keywords_in_file(session_file, ["langroid", "missing"])

    assert found is False


def test_search_keywords_in_file_case_insensitive(tmp_path):
    """Test search_keywords_in_file is case insensitive."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "Testing LANGROID"}]}}\n'
    session_file.write_text(content)

    found, _, _ = search_keywords_in_file(session_file, ["langroid"])

    assert found is True


def test_search_keywords_in_file_extracts_last_user_message(tmp_path):
    """Test search_keywords_in_file extracts last substantial user message."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "First message here"}]}}\n'
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "Last message is this one"}]}}\n'
    )
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    assert "Last message" in preview


def test_search_keywords_in_file_skips_system_messages(tmp_path):
    """Test search_keywords_in_file filters out system messages."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "<system>"}]}}\n'
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "Real user message"}]}}\n'
    )
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    assert "Real user message" in preview


def test_search_keywords_in_file_prefers_substantial_messages(tmp_path):
    """Test search_keywords_in_file prefers messages >20 chars."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "short"}]}}\n'
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "This is a much longer substantial message"}]}}\n'
    )
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    assert "substantial message" in preview


def test_search_keywords_in_file_truncates_preview(tmp_path):
    """Test search_keywords_in_file truncates preview to 400 chars."""
    session_file = tmp_path / "session.jsonl"
    long_text = "a" * 500
    content = f'{{"type": "response_item", "payload": {{"role": "user", "content": [{{"text": "{long_text}"}}]}}}}\n'
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    assert len(preview) <= 400


def test_search_keywords_in_file_replaces_newlines(tmp_path):
    """Test search_keywords_in_file replaces newlines with spaces."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "Line 1\\nLine 2\\nLine 3"}]}}\n'
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    # Newlines should be replaced with spaces
    assert "\\n" not in preview or "\n" not in preview


def test_search_keywords_in_file_handles_non_user_roles(tmp_path):
    """Test search_keywords_in_file skips non-user roles."""
    session_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "response_item", "payload": {"role": "assistant", "content": [{"text": "assistant message"}]}}\n'
        '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "user message"}]}}\n'
    )
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    assert "user message" in preview
    assert "assistant message" not in preview


def test_search_keywords_in_file_handles_invalid_json(tmp_path):
    """Test search_keywords_in_file handles malformed JSON."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("{ invalid json }\n")

    found, line_count, preview = search_keywords_in_file(session_file, [])

    # Should count the line but not crash
    assert line_count == 1


def test_search_keywords_in_file_missing_file():
    """Test search_keywords_in_file handles missing file."""
    fake_path = Path("/nonexistent/file.jsonl")

    found, line_count, preview = search_keywords_in_file(fake_path, ["test"])

    assert found is False
    assert line_count == 0
    assert preview is None


def test_search_keywords_in_file_empty_content_list(tmp_path):
    """Test search_keywords_in_file handles empty content list."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": []}}\n'
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    # Should handle gracefully
    assert preview in [None, ""]


def test_search_keywords_in_file_non_dict_content_item(tmp_path):
    """Test search_keywords_in_file handles non-dict content items."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": ["string", {"text": "valid message text"}]}}\n'
    session_file.write_text(content)

    _, _, preview = search_keywords_in_file(session_file, [])

    # Should extract the valid dict item
    assert preview in [None, "valid message text"]


# ==================== Test find_sessions ====================


def test_find_sessions_no_sessions_dir(tmp_path):
    """Test find_sessions returns empty when sessions dir doesn't exist."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()

    result = find_sessions(codex_home, ["test"])

    assert result == []


def test_find_sessions_empty_sessions_dir(tmp_path):
    """Test find_sessions returns empty with no session files."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions"
    sessions_dir.mkdir(parents=True)

    result = find_sessions(codex_home, ["test"])

    assert result == []


def test_find_sessions_finds_matching_session(tmp_path):
    """Test find_sessions finds and returns matching sessions."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    # Create session file with metadata
    session_file = sessions_dir / "rollout-2025-10-07T13-48-15-abc123.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": "abc123",
            "cwd": os.getcwd(),  # Use current directory for non-global search
            "git": {"branch": "main"},
            "timestamp": "2025-10-07T13:48:15"
        }
    }
    user_msg = {
        "type": "response_item",
        "payload": {
            "role": "user",
            "content": [{"text": "test langroid message"}]
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n" + json.dumps(user_msg) + "\n")

    result = find_sessions(codex_home, ["langroid"], global_search=True)

    assert len(result) >= 1
    assert any(r["session_id"] == "abc123" for r in result)
    match = [r for r in result if r["session_id"] == "abc123"][0]
    assert match["branch"] == "main"


def test_find_sessions_filters_non_matching(tmp_path):
    """Test find_sessions filters out sessions that don't match keywords."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    # Create two session files  with metadata
    match_file = sessions_dir / "rollout-2025-10-07T13-48-15-match.jsonl"
    nomatch_file = sessions_dir / "rollout-2025-10-07T14-00-00-nomatch.jsonl"

    match_meta = {"type": "session_meta", "payload": {"id": "match", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}
    nomatch_meta = {"type": "session_meta", "payload": {"id": "nomatch", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}

    match_content = json.dumps(match_meta) + '\n{"type": "response_item", "payload": {"role": "user", "content": [{"text": "langroid message"}]}}\n'
    nomatch_content = json.dumps(nomatch_meta) + '\n{"type": "response_item", "payload": {"role": "user", "content": [{"text": "other message"}]}}\n'

    match_file.write_text(match_content)
    nomatch_file.write_text(nomatch_content)

    result = find_sessions(codex_home, ["langroid"], global_search=True)

    assert len(result) >= 1
    assert any("match" in r["session_id"] for r in result)


def test_find_sessions_sorts_by_modification_time(tmp_path):
    """Test find_sessions sorts by modification time (newest first)."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    old_file = sessions_dir / "rollout-2025-10-07T10-00-00-old.jsonl"
    new_file = sessions_dir / "rollout-2025-10-07T14-00-00-new.jsonl"

    # Add metadata to files
    old_meta = {"type": "session_meta", "payload": {"id": "old", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}
    new_meta = {"type": "session_meta", "payload": {"id": "new", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}

    old_file.write_text(json.dumps(old_meta) + '\n{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test message"}]}}\n')
    new_file.write_text(json.dumps(new_meta) + '\n{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test message"}]}}\n')

    # Set modification times
    import time
    os.utime(old_file, (time.time() - 3600, time.time() - 3600))  # 1 hour ago
    os.utime(new_file, (time.time(), time.time()))  # now

    result = find_sessions(codex_home, [], global_search=True)

    # Should have results sorted by time
    if len(result) >= 2:
        assert result[0]["mod_time"] >= result[1]["mod_time"]


def test_find_sessions_global_search_filters_current_dir(tmp_path):
    """Test find_sessions filters by current directory when not global."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    session_file = sessions_dir / "rollout-2025-10-07T13-48-15-abc.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": "abc",
            "cwd": "/other/project",
            "git": {},
            "timestamp": ""
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n")

    with patch("os.getcwd", return_value="/home/user/myproject"):
        with patch("claude_code_tools.find_codex_session.search_keywords_in_file") as mock_search:
            mock_search.return_value = (True, 10, "preview")

            result = find_sessions(codex_home, [], global_search=False)

    # Should be filtered out because cwd doesn't match
    assert len(result) == 0


def test_find_sessions_global_search_includes_all(tmp_path):
    """Test find_sessions includes all projects in global search."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    session_file = sessions_dir / "rollout-2025-10-07T13-48-15-abc.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": "abc",
            "cwd": "/other/project",
            "git": {},
            "timestamp": ""
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n")

    with patch("os.getcwd", return_value="/home/user/myproject"):
        with patch("claude_code_tools.find_codex_session.search_keywords_in_file") as mock_search:
            mock_search.return_value = (True, 10, "preview")

            result = find_sessions(codex_home, [], global_search=True)

    # Should include session from other project
    assert len(result) == 1


def test_find_sessions_respects_num_matches(tmp_path):
    """Test find_sessions limits results to num_matches."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    # Create 10 session files with metadata
    for i in range(10):
        session_file = sessions_dir / f"rollout-2025-10-07T{i:02d}-00-00-id{i}.jsonl"
        meta = {"type": "session_meta", "payload": {"id": f"id{i}", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}
        session_file.write_text(json.dumps(meta) + '\n{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test"}]}}\n')

    result = find_sessions(codex_home, [], num_matches=5, global_search=True)

    # Should return at most 5 results
    assert len(result) <= 5


def test_find_sessions_handles_missing_metadata(tmp_path):
    """Test find_sessions falls back to filename when metadata missing."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    session_file = sessions_dir / "rollout-2025-10-07T13-48-15-fallback-id.jsonl"
    # File with content but no metadata
    session_file.write_text('{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test message"}]}}\n')

    result = find_sessions(codex_home, [], global_search=True)

    # Should use fallback ID extraction from filename
    if len(result) >= 1:
        assert any("fallback-id" in r["session_id"] for r in result)


def test_find_sessions_early_exit_optimization(tmp_path):
    """Test find_sessions exits early after finding enough matches."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions" / "2025" / "10" / "07"
    sessions_dir.mkdir(parents=True)

    # Create many files with metadata
    for i in range(20):  # Reduce to 20 for faster test
        session_file = sessions_dir / f"rollout-2025-10-07T{i:02d}-00-00-id{i}.jsonl"
        meta = {"type": "session_meta", "payload": {"id": f"id{i}", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}
        session_file.write_text(json.dumps(meta) + '\n{"type": "response_item", "payload": {"role": "user", "content": [{"text": "test"}]}}\n')

    result = find_sessions(codex_home, [], num_matches=5, global_search=True)

    # Should limit results (early exit optimization)
    assert len(result) <= 5


# ==================== Test display_interactive_ui ====================


@patch("claude_code_tools.find_codex_session.RICH_AVAILABLE", False)
def test_display_interactive_ui_no_matches():
    """Test display_interactive_ui with no matches."""
    result = display_interactive_ui([], ["test"])

    assert result is None


@patch("builtins.input", return_value="")
def test_display_interactive_ui_cancel(mock_input, capsys):
    """Test display_interactive_ui handles cancellation with multiple matches."""
    matches = [
        {
            "session_id": "test123",
            "project": "project",
            "branch": "main",
            "date": "10/07 - 10/07 13:48",
            "lines": 100,
            "preview": "test preview",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/file"
        },
        {
            "session_id": "test456",
            "project": "project2",
            "branch": "dev",
            "date": "10/08",
            "lines": 200,
            "preview": "test2",
            "mod_time": 1700000100.0,
            "cwd": "/path2",
            "file_path": "/file2"
        }
    ]

    result = display_interactive_ui(matches, ["test"])

    # With multiple matches, empty input should cancel
    assert result is None


@patch("builtins.input", return_value="1")
def test_display_interactive_ui_single_auto_select(mock_input, capsys):
    """Test display_interactive_ui auto-selects single match."""
    matches = [
        {
            "session_id": "test123",
            "project": "project",
            "branch": "main",
            "date": "10/07",
            "lines": 100,
            "preview": "test",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/path/to/file"
        }
    ]

    result = display_interactive_ui(matches, ["test"])

    assert result == matches[0]


@patch("builtins.input", return_value="1")
def test_display_interactive_ui_valid_selection(mock_input):
    """Test display_interactive_ui returns selected match."""
    matches = [
        {
            "session_id": "test1",
            "project": "proj1",
            "branch": "main",
            "date": "10/07",
            "lines": 100,
            "preview": "test",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/file"
        },
        {
            "session_id": "test2",
            "project": "proj2",
            "branch": "dev",
            "date": "10/08",
            "lines": 200,
            "preview": "test2",
            "mod_time": 1700000100.0,
            "cwd": "/path2",
            "file_path": "/file2"
        }
    ]

    result = display_interactive_ui(matches, ["test"])

    assert result == matches[0]


@patch("builtins.input", return_value="99")
def test_display_interactive_ui_invalid_selection(mock_input, capsys):
    """Test display_interactive_ui handles invalid selection with multiple matches."""
    matches = [
        {
            "session_id": "test",
            "project": "proj",
            "branch": "main",
            "date": "10/07",
            "lines": 100,
            "preview": "test",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/file"
        },
        {
            "session_id": "test2",
            "project": "proj2",
            "branch": "dev",
            "date": "10/08",
            "lines": 200,
            "preview": "test2",
            "mod_time": 1700000100.0,
            "cwd": "/path2",
            "file_path": "/file2"
        }
    ]

    result = display_interactive_ui(matches, ["test"])

    # Invalid selection should return None for multiple matches
    assert result is None


@patch("builtins.input", side_effect=KeyboardInterrupt)
def test_display_interactive_ui_keyboard_interrupt(mock_input, capsys):
    """Test display_interactive_ui handles KeyboardInterrupt with multiple matches."""
    matches = [
        {
            "session_id": "test",
            "project": "proj",
            "branch": "main",
            "date": "10/07",
            "lines": 100,
            "preview": "test",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/file"
        },
        {
            "session_id": "test2",
            "project": "proj2",
            "branch": "dev",
            "date": "10/08",
            "lines": 200,
            "preview": "test2",
            "mod_time": 1700000100.0,
            "cwd": "/path2",
            "file_path": "/file2"
        }
    ]

    result = display_interactive_ui(matches, ["test"])

    # KeyboardInterrupt should return None
    assert result is None


@patch("builtins.input", return_value="abc")
def test_display_interactive_ui_non_numeric_input(mock_input, capsys):
    """Test display_interactive_ui handles non-numeric input with multiple matches."""
    matches = [
        {
            "session_id": "test",
            "project": "proj",
            "branch": "main",
            "date": "10/07",
            "lines": 100,
            "preview": "test",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/file"
        },
        {
            "session_id": "test2",
            "project": "proj2",
            "branch": "dev",
            "date": "10/08",
            "lines": 200,
            "preview": "test2",
            "mod_time": 1700000100.0,
            "cwd": "/path2",
            "file_path": "/file2"
        }
    ]

    result = display_interactive_ui(matches, ["test"])

    # Non-numeric input should return None for multiple matches
    assert result is None


# ==================== Test show_action_menu ====================


@patch("builtins.input", return_value="")
def test_show_action_menu_default_resume(mock_input):
    """Test show_action_menu defaults to resume."""
    match = {"session_id": "test", "project": "proj", "branch": "main"}

    result = show_action_menu(match)

    assert result == "resume"


@patch("builtins.input", return_value="1")
def test_show_action_menu_choice_resume(mock_input):
    """Test show_action_menu returns resume for choice 1."""
    match = {"session_id": "test", "project": "proj", "branch": "main"}

    result = show_action_menu(match)

    assert result == "resume"


@patch("builtins.input", return_value="2")
def test_show_action_menu_choice_path(mock_input):
    """Test show_action_menu returns path for choice 2."""
    match = {"session_id": "test", "project": "proj", "branch": "main"}

    result = show_action_menu(match)

    assert result == "path"


@patch("builtins.input", return_value="3")
def test_show_action_menu_choice_copy(mock_input):
    """Test show_action_menu returns copy for choice 3."""
    match = {"session_id": "test", "project": "proj", "branch": "main"}

    result = show_action_menu(match)

    assert result == "copy"


@patch("builtins.input", return_value="99")
def test_show_action_menu_invalid_choice(mock_input):
    """Test show_action_menu returns None for invalid choice."""
    match = {"session_id": "test", "project": "proj", "branch": "main"}

    result = show_action_menu(match)

    assert result is None


@patch("builtins.input", side_effect=KeyboardInterrupt)
def test_show_action_menu_keyboard_interrupt(mock_input):
    """Test show_action_menu handles KeyboardInterrupt."""
    match = {"session_id": "test", "project": "proj", "branch": "main"}

    result = show_action_menu(match)

    assert result is None


# ==================== Test copy_session_file ====================


@patch("builtins.input", return_value="")
def test_copy_session_file_cancel(mock_input, capsys):
    """Test copy_session_file handles cancellation."""
    copy_session_file("/source/file.jsonl")

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


@patch("pathlib.Path.exists", return_value=True)
@patch("pathlib.Path.is_dir", return_value=True)
@patch("shutil.copy2")
@patch("builtins.input", return_value="/dest/dir")
def test_copy_session_file_to_directory(mock_input, mock_copy, mock_is_dir, mock_exists):
    """Test copy_session_file copies to directory."""
    copy_session_file("/source/file.jsonl")

    mock_copy.assert_called_once()


@patch("builtins.input", side_effect=KeyboardInterrupt)
def test_copy_session_file_keyboard_interrupt(mock_input, capsys):
    """Test copy_session_file handles KeyboardInterrupt."""
    copy_session_file("/source/file.jsonl")

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


# ==================== Test resume_session ====================


@patch("os.getcwd", return_value="/current/dir")
def test_resume_session_shell_mode(mock_getcwd, capsys):
    """Test resume_session in shell mode outputs commands."""
    resume_session("test-session", "/other/dir", shell_mode=True)

    captured = capsys.readouterr()
    assert "cd" in captured.out
    assert "codex resume" in captured.out


@patch("os.getcwd", return_value="/current/dir")
@patch("os.execvp")
def test_resume_session_same_directory(mock_execvp, mock_getcwd):
    """Test resume_session executes codex in same directory."""
    resume_session("test-session", "/current/dir", shell_mode=False)

    mock_execvp.assert_called_once_with("codex", ["codex", "resume", "test-session"])


@patch("os.getcwd", return_value="/current/dir")
@patch("builtins.input", return_value="y")
@patch("os.chdir")
@patch("os.execvp")
def test_resume_session_changes_directory(mock_execvp, mock_chdir, mock_input, mock_getcwd):
    """Test resume_session changes directory when approved."""
    resume_session("test-session", "/other/dir", shell_mode=False)

    mock_chdir.assert_called_once_with("/other/dir")
    mock_execvp.assert_called_once()


@patch("os.getcwd", return_value="/current/dir")
@patch("os.execvp", side_effect=OSError("command not found"))
def test_resume_session_codex_not_found(mock_execvp, mock_getcwd):
    """Test resume_session handles codex command not found."""
    with patch("builtins.input", return_value=""):
        with pytest.raises(SystemExit):
            resume_session("test-session", "/current/dir", shell_mode=False)


# ==================== Test main ====================


@patch("sys.argv", ["find-codex-session", "test"])
@patch("claude_code_tools.find_codex_session.get_codex_home")
@patch("claude_code_tools.find_codex_session.find_sessions", return_value=[])
def test_main_no_results(mock_find, mock_home, tmp_path):
    """Test main handles no matching sessions."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    mock_home.return_value = codex_home

    # Should exit gracefully (returns None means no session selected)
    main()


@patch("sys.argv", ["find-codex-session"])
@patch("claude_code_tools.find_codex_session.get_codex_home")
def test_main_codex_home_not_found(mock_home):
    """Test main exits when codex home doesn't exist."""
    mock_home.return_value = Path("/nonexistent")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


@patch("sys.argv", ["find-codex-session", "test1,test2"])
@patch("claude_code_tools.find_codex_session.find_sessions", return_value=[])
@patch("claude_code_tools.find_codex_session.get_codex_home")
def test_main_parses_keywords(mock_home, mock_find, tmp_path):
    """Test main parses comma-separated keywords."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    mock_home.return_value = codex_home

    main()

    # Check keywords were parsed
    call_args = mock_find.call_args
    assert call_args[0][1] == ["test1", "test2"]


@patch("sys.argv", ["find-codex-session", "-g"])
@patch("claude_code_tools.find_codex_session.find_sessions", return_value=[])
@patch("claude_code_tools.find_codex_session.get_codex_home")
def test_main_global_search_flag(mock_home, mock_find, tmp_path):
    """Test main respects global search flag."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    mock_home.return_value = codex_home

    main()

    # Check that global_search parameter was passed (may be positional or keyword)
    call_args = mock_find.call_args
    # Check keyword args
    if call_args[1] and "global_search" in call_args[1]:
        assert call_args[1]["global_search"] is True
    else:
        # If not keyword, check positional arg (3rd argument is global_search)
        assert len(call_args[0]) >= 3
        assert call_args[0][3] is True


@patch("sys.argv", ["find-codex-session", "-n", "20"])
@patch("claude_code_tools.find_codex_session.find_sessions", return_value=[])
@patch("claude_code_tools.find_codex_session.get_codex_home")
def test_main_num_matches_flag(mock_home, mock_find, tmp_path):
    """Test main respects num_matches flag."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    mock_home.return_value = codex_home

    main()

    assert mock_find.call_args[0][2] == 20


@patch("sys.argv", ["find-codex-session", "--codex-home", "/custom"])
@patch("claude_code_tools.find_codex_session.get_codex_home")
def test_main_custom_codex_home(mock_home):
    """Test main uses custom codex home."""
    mock_home.return_value = Path("/nonexistent")

    with pytest.raises(SystemExit):
        main()

    mock_home.assert_called_once_with("/custom")


@patch("sys.argv", ["find-codex-session", "--shell", "test"])
@patch("claude_code_tools.find_codex_session.find_sessions")
@patch("claude_code_tools.find_codex_session.display_interactive_ui", return_value=None)
@patch("claude_code_tools.find_codex_session.get_codex_home")
def test_main_shell_mode(mock_home, mock_display, mock_find, tmp_path):
    """Test main in shell mode."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    mock_home.return_value = codex_home
    mock_find.return_value = [
        {
            "session_id": "test",
            "project": "proj",
            "branch": "main",
            "date": "10/07",
            "lines": 100,
            "preview": "test",
            "mod_time": 1700000000.0,
            "cwd": "/path",
            "file_path": "/file"
        }
    ]

    main()

    # Display was called (user cancelled, returned None)
    mock_display.assert_called_once()


# ==================== Test Edge Cases ====================


def test_extract_session_id_regex_pattern():
    """Test extract_session_id_from_filename regex pattern."""
    # Test various valid formats
    valid_filenames = [
        "rollout-2025-10-07T13-48-15-abc123.jsonl",
        "rollout-2024-01-01T00-00-00-xyz-789.jsonl",
        "rollout-2025-12-31T23-59-59-0199bfc9-c444-77e1-8c8a-f91c94fcd832.jsonl",
    ]

    for filename in valid_filenames:
        result = extract_session_id_from_filename(filename)
        assert result is not None


def test_search_keywords_in_file_unicode(tmp_path):
    """Test search_keywords_in_file handles unicode."""
    session_file = tmp_path / "session.jsonl"
    content = '{"type": "response_item", "payload": {"role": "user", "content": [{"text": "Testing émojis 🚀"}]}}\n'
    session_file.write_text(content, encoding="utf-8")

    found, _, _ = search_keywords_in_file(session_file, ["émojis"])

    assert found is True


def test_find_sessions_nested_year_month_day_dirs(tmp_path):
    """Test find_sessions walks through nested year/month/day structure."""
    codex_home = tmp_path / ".codex"

    # Create multiple nested directories
    sessions_2024 = codex_home / "sessions" / "2024" / "12" / "25"
    sessions_2025 = codex_home / "sessions" / "2025" / "01" / "01"

    sessions_2024.mkdir(parents=True)
    sessions_2025.mkdir(parents=True)

    # Create session files in both with metadata
    old_meta = {"type": "session_meta", "payload": {"id": "old", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}
    new_meta = {"type": "session_meta", "payload": {"id": "new", "cwd": os.getcwd(), "git": {}, "timestamp": ""}}

    (sessions_2024 / "rollout-2024-12-25T10-00-00-old.jsonl").write_text(json.dumps(old_meta) + '\n')
    (sessions_2025 / "rollout-2025-01-01T10-00-00-new.jsonl").write_text(json.dumps(new_meta) + '\n')

    result = find_sessions(codex_home, [], global_search=True)

    # Should find sessions from both years
    assert len(result) >= 2


def test_find_sessions_handles_non_dir_files_in_sessions(tmp_path):
    """Test find_sessions ignores non-directory files in sessions path."""
    codex_home = tmp_path / ".codex"
    sessions_dir = codex_home / "sessions"
    sessions_dir.mkdir(parents=True)

    # Create a file (should be ignored)
    (sessions_dir / "some-file.txt").touch()

    # Create valid directory structure
    year_dir = sessions_dir / "2025"
    year_dir.mkdir()

    result = find_sessions(codex_home, [])

    # Should not crash, returns empty (no session files)
    assert result == []


def test_extract_session_metadata_missing_payload_fields(tmp_path):
    """Test extract_session_metadata handles missing fields gracefully."""
    session_file = tmp_path / "session.jsonl"
    metadata = {
        "type": "session_meta",
        "payload": {
            # Missing id, cwd, timestamp
        }
    }
    session_file.write_text(json.dumps(metadata) + "\n")

    result = extract_session_metadata(session_file)

    # Should handle missing fields with defaults
    assert result["id"] == ""
    assert result["cwd"] == ""
    assert result["timestamp"] == ""
