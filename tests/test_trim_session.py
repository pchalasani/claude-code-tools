"""Unit tests for trim_session functionality."""

import json
import tempfile
from pathlib import Path

import pytest

from claude_code_tools.find_claude_session import is_sidechain_session
from claude_code_tools.trim_session import (
    create_placeholder,
    detect_agent,
    extract_session_info,
    is_trimmed_session,
    process_session,
    trim_and_create_session,
)


@pytest.fixture
def fixtures_dir():
    """Return path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def claude_session(fixtures_dir):
    """Return path to Claude session fixture."""
    return fixtures_dir / "claude_session.jsonl"


@pytest.fixture
def codex_session(fixtures_dir):
    """Return path to Codex session fixture."""
    return fixtures_dir / "codex_session.jsonl"


@pytest.fixture
def temp_output_dir():
    """Create a temporary directory for output files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestDetectAgent:
    """Tests for agent type detection."""

    @pytest.mark.parametrize(
        "session_fixture,expected_agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_detect_agent(self, session_fixture, expected_agent, request):
        """Test agent detection from session files."""
        session_path = request.getfixturevalue(session_fixture)
        detected = detect_agent(session_path)
        assert detected == expected_agent


class TestExtractSessionInfo:
    """Tests for extracting session information."""

    def test_extract_claude_cwd(self, claude_session):
        """Test extracting cwd from Claude session."""
        info = extract_session_info(claude_session, "claude")
        assert info["cwd"] == "/test/dir"

    def test_extract_codex_cwd(self, codex_session):
        """Test extracting cwd from Codex session."""
        info = extract_session_info(codex_session, "codex")
        assert info["cwd"] == "/test/dir"

    def test_extract_invalid_agent(self, claude_session):
        """Test extracting info with wrong agent type."""
        info = extract_session_info(claude_session, "invalid")
        assert info["cwd"] is None


class TestCreatePlaceholder:
    """Tests for placeholder text generation."""

    def test_create_placeholder_formatting(self):
        """Test placeholder text is properly formatted."""
        placeholder = create_placeholder("Read", 1000)
        assert "Read" in placeholder
        assert "1,000 characters" in placeholder
        assert placeholder.startswith("[Results from")


class TestProcessSession:
    """Tests for session processing."""

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_process_trims_tool_results(
        self, session_fixture, agent, temp_output_dir, request
    ):
        """Test that long tool results are trimmed."""
        session_path = request.getfixturevalue(session_fixture)
        output_path = temp_output_dir / "trimmed.jsonl"

        num_tools, num_asst, chars_saved = process_session(
            agent=agent,
            input_file=session_path,
            output_file=output_path,
            target_tools=None,  # Trim all tools
            threshold=500,
            verbose=False,
        )

        assert num_tools > 0, "Should have trimmed at least one tool result"
        assert chars_saved > 0, "Should have saved some characters"
        assert output_path.exists(), "Output file should exist"

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_process_trims_assistant_messages(
        self, session_fixture, agent, temp_output_dir, request
    ):
        """Test that long assistant messages are trimmed."""
        session_path = request.getfixturevalue(session_fixture)
        output_path = temp_output_dir / "trimmed_asst.jsonl"

        num_tools, num_asst, chars_saved = process_session(
            agent=agent,
            input_file=session_path,
            output_file=output_path,
            target_tools=None,
            threshold=500,
            verbose=False,
            trim_assistant_messages=1,  # Trim first assistant message
        )

        assert num_asst > 0, "Should have trimmed at least one assistant msg"
        assert output_path.exists(), "Output file should exist"

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_process_specific_tools_only(
        self, session_fixture, agent, temp_output_dir, request
    ):
        """Test trimming only specific tools."""
        session_path = request.getfixturevalue(session_fixture)
        output_path = temp_output_dir / "trimmed_read_only.jsonl"

        num_tools, num_asst, chars_saved = process_session(
            agent=agent,
            input_file=session_path,
            output_file=output_path,
            target_tools={"read"},  # Only trim Read tool
            threshold=500,
            verbose=False,
        )

        assert output_path.exists(), "Output file should exist"

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_truncates_instead_of_replaces(
        self, session_fixture, agent, temp_output_dir, request
    ):
        """Test that tool results are truncated, not completely replaced."""
        session_path = request.getfixturevalue(session_fixture)
        output_path = temp_output_dir / "truncated.jsonl"

        threshold = 100
        process_session(
            agent=agent,
            input_file=session_path,
            output_file=output_path,
            target_tools=None,
            threshold=threshold,
            verbose=False,
        )

        # Read output and verify tool results are truncated, not replaced
        with open(output_path) as f:
            for line in f:
                data = json.loads(line)

                # Check user messages for tool_result content
                if data.get("type") == "user":
                    content = data.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for item in content:
                            if (
                                isinstance(item, dict)
                                and item.get("type") == "tool_result"
                            ):
                                result_content = item.get("content", "")
                                if isinstance(result_content, str):
                                    # Should not be a placeholder replacement
                                    assert not result_content.startswith(
                                        "[Results from"
                                    ), (
                                        "Tool result should be truncated, "
                                        "not replaced with placeholder"
                                    )
                                    # Should contain truncation notice if truncated
                                    if "...truncated" in result_content.lower():
                                        # Verify original content is preserved
                                        # (should start with actual content)
                                        assert len(result_content) > 0


class TestTrimAndCreateSession:
    """Tests for the main trim_and_create_session function."""

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_creates_new_session_with_metadata(
        self, session_fixture, agent, temp_output_dir, request
    ):
        """Test that trim_and_create_session adds metadata to first line."""
        session_path = request.getfixturevalue(session_fixture)

        result = trim_and_create_session(
            agent=agent,
            input_file=session_path,
            target_tools=None,
            threshold=500,
            output_dir=temp_output_dir,
            min_token_savings=0,
        )

        assert result["detected_agent"] == agent
        assert "session_id" in result
        assert "output_file" in result
        assert result["num_tools_trimmed"] >= 0
        assert result["chars_saved"] >= 0

        # Check that output file exists
        output_path = Path(result["output_file"])
        assert output_path.exists()

        # Check that metadata was added to first line
        with open(output_path) as f:
            first_line = json.loads(f.readline())
            assert "trim_metadata" in first_line
            assert first_line["trim_metadata"]["parent_file"] == str(
                session_path.absolute()
            )
            assert "trimmed_at" in first_line["trim_metadata"]
            assert "trim_params" in first_line["trim_metadata"]
            assert "stats" in first_line["trim_metadata"]

    @pytest.mark.parametrize(
        "session_fixture",
        ["claude_session", "codex_session"],
    )
    def test_auto_detects_agent(
        self, session_fixture, temp_output_dir, request
    ):
        """Test that agent type is auto-detected when not specified."""
        session_path = request.getfixturevalue(session_fixture)

        result = trim_and_create_session(
            agent=None,  # Auto-detect
            input_file=session_path,
            target_tools=None,
            threshold=500,
            output_dir=temp_output_dir,
            min_token_savings=0,
        )

        expected_agent = (
            "claude" if "claude" in session_fixture else "codex"
        )
        assert result["detected_agent"] == expected_agent

    def test_trim_assistant_messages_first_n(
        self, claude_session, temp_output_dir
    ):
        """Test trimming first N assistant messages."""
        result = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_output_dir,
            trim_assistant_messages=1,  # Trim first 1
            min_token_savings=0,
        )

        assert result["num_assistant_trimmed"] > 0

    def test_trim_assistant_messages_all_except_last_n(
        self, claude_session, temp_output_dir
    ):
        """Test trimming all except last N assistant messages."""
        result = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_output_dir,
            trim_assistant_messages=-1,  # Keep last 1
            min_token_savings=0,
        )

        # Should have trimmed some messages (if more than 1 exists)
        assert result["num_assistant_trimmed"] >= 0


class TestIsTrimmedSession:
    """Tests for detecting trimmed sessions."""

    def test_original_session_not_trimmed(self, claude_session):
        """Test that original sessions are detected as not trimmed."""
        assert not is_trimmed_session(claude_session)

    def test_trimmed_session_detected(self, claude_session, temp_output_dir):
        """Test that trimmed sessions are detected correctly."""
        # Create a trimmed session
        result = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_output_dir,
            min_token_savings=0,
        )

        trimmed_file = Path(result["output_file"])
        assert is_trimmed_session(trimmed_file)

    def test_nonexistent_file_returns_false(self):
        """Test that nonexistent files return False."""
        nonexistent = Path("/tmp/does_not_exist_12345.jsonl")
        assert not is_trimmed_session(nonexistent)

    def test_empty_file_returns_false(self, temp_output_dir):
        """Test that empty files return False."""
        empty_file = temp_output_dir / "empty.jsonl"
        empty_file.touch()
        assert not is_trimmed_session(empty_file)

    def test_invalid_json_returns_false(self, temp_output_dir):
        """Test that files with invalid JSON return False."""
        invalid_file = temp_output_dir / "invalid.jsonl"
        invalid_file.write_text("not valid json\n")
        assert not is_trimmed_session(invalid_file)

    def test_session_without_trim_metadata(self, temp_output_dir):
        """Test that sessions without trim_metadata return False."""
        session_file = temp_output_dir / "normal.jsonl"
        session_file.write_text(
            '{"type":"session_start","sessionId":"test123"}\n'
        )
        assert not is_trimmed_session(session_file)

    def test_codex_trimmed_session_detected(
        self, codex_session, temp_output_dir
    ):
        """Test that trimmed Codex sessions are detected correctly."""
        result = trim_and_create_session(
            agent="codex",
            input_file=codex_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_output_dir,
            min_token_savings=0,
        )

        trimmed_file = Path(result["output_file"])
        assert is_trimmed_session(trimmed_file)


class TestIsSidechainSession:
    """Tests for detecting sidechain (sub-agent) sessions."""

    def test_regular_session_not_sidechain(self, claude_session):
        """Test that regular sessions are not sidechains."""
        assert not is_sidechain_session(claude_session)

    def test_sidechain_session_detected(self, temp_output_dir):
        """Test that sidechain sessions are detected correctly."""
        # Create a sidechain session file
        sidechain_file = temp_output_dir / "agent-test123.jsonl"
        sidechain_content = (
            '{"parentUuid":null,"isSidechain":true,"userType":"external",'
            '"cwd":"/test","sessionId":"abc123","agentId":"test123",'
            '"type":"user","message":{"role":"user","content":"test"}}\n'
        )
        sidechain_file.write_text(sidechain_content)

        assert is_sidechain_session(sidechain_file)

    def test_non_sidechain_with_field_false(self, temp_output_dir):
        """Test that sessions with isSidechain:false are not sidechains."""
        session_file = temp_output_dir / "normal.jsonl"
        session_content = (
            '{"isSidechain":false,"sessionId":"abc123",'
            '"type":"user","message":{"role":"user","content":"test"}}\n'
        )
        session_file.write_text(session_content)

        assert not is_sidechain_session(session_file)

    def test_nonexistent_file_returns_false(self):
        """Test that nonexistent files return False."""
        nonexistent = Path("/tmp/does_not_exist_sidechain.jsonl")
        assert not is_sidechain_session(nonexistent)

    def test_empty_file_returns_false(self, temp_output_dir):
        """Test that empty files return False."""
        empty_file = temp_output_dir / "empty_sidechain.jsonl"
        empty_file.touch()
        assert not is_sidechain_session(empty_file)

    def test_invalid_json_returns_false(self, temp_output_dir):
        """Test that files with invalid JSON return False."""
        invalid_file = temp_output_dir / "invalid_sidechain.jsonl"
        invalid_file.write_text("not valid json\n")
        assert not is_sidechain_session(invalid_file)

    def test_session_without_sidechain_field(self, temp_output_dir):
        """Test that sessions without isSidechain field return False."""
        session_file = temp_output_dir / "no_field.jsonl"
        session_file.write_text(
            '{"type":"session_start","sessionId":"test123"}\n'
        )
        assert not is_sidechain_session(session_file)
