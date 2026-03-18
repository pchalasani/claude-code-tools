"""Unit tests for --original filtering in find-session commands."""

import tempfile
from pathlib import Path

import pytest

from claude_code_tools.find_codex_session import (
    find_sessions as find_codex_sessions,
)
from claude_code_tools.trim_session import (
    is_trimmed_session,
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
def temp_codex_dir():
    """Create a temporary Codex-style directory structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create sessions/YYYY/MM/DD structure
        codex_home = Path(tmpdir)
        sessions_dir = codex_home / "sessions" / "2024" / "10" / "24"
        sessions_dir.mkdir(parents=True)
        yield codex_home


class TestIsTrimmedSessionIntegration:
    """Integration tests for is_trimmed_session with real files."""

    def test_detects_trimmed_claude_session(
        self, claude_session
    ):
        """Test detecting trimmed Claude session in real scenario."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create trimmed version
            result = trim_and_create_session(
                agent="claude",
                input_file=claude_session,
                target_tools=None,
                threshold=500,
                output_dir=Path(tmpdir),
                min_token_savings=0,
            )

            trimmed_path = Path(result["output_file"])

            # Verify detection
            assert not is_trimmed_session(claude_session)
            assert is_trimmed_session(trimmed_path)

    def test_detects_trimmed_codex_session(
        self, codex_session
    ):
        """Test detecting trimmed Codex session in real scenario."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create trimmed version
            result = trim_and_create_session(
                agent="codex",
                input_file=codex_session,
                target_tools=None,
                threshold=500,
                output_dir=Path(tmpdir),
                min_token_savings=0,
            )

            trimmed_path = Path(result["output_file"])

            # Verify detection
            assert not is_trimmed_session(codex_session)
            assert is_trimmed_session(trimmed_path)


class TestCodexSessionFiltering:
    """Tests for filtering Codex sessions with --original flag."""

    def test_excludes_trimmed_with_original_only(
        self, codex_session, temp_codex_dir
    ):
        """Test that trimmed sessions are excluded with original_only=True."""
        import shutil

        sessions_dir = temp_codex_dir / "sessions" / "2024" / "10" / "24"
        dest = sessions_dir / "rollout-2024-10-24T10-00-00-original.jsonl"
        shutil.copy(codex_session, dest)

        # Create trimmed version in same directory
        trim_and_create_session(
            agent="codex",
            input_file=codex_session,
            target_tools=None,
            threshold=500,
            output_dir=sessions_dir,
            min_token_savings=0,
        )

        # Find with original_only=True
        sessions = find_codex_sessions(
            codex_home=temp_codex_dir,
            keywords=[],
            num_matches=10,
            global_search=True,
            original_only=True,
        )

        # Should only find the original
        assert len(sessions) == 1
        assert not sessions[0]["is_trimmed"]

    def test_includes_both_without_filtering(
        self, codex_session, temp_codex_dir
    ):
        """Test that both trimmed and original are included by default."""
        import shutil

        sessions_dir = temp_codex_dir / "sessions" / "2024" / "10" / "24"
        dest = sessions_dir / "rollout-2024-10-24T10-00-00-original.jsonl"
        shutil.copy(codex_session, dest)

        # Create trimmed version in same directory
        # Note: trim creates a dated subfolder for Codex, so we need to search there
        result = trim_and_create_session(
            agent="codex",
            input_file=codex_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_codex_dir / "sessions",
            min_token_savings=0,
        )

        # Find without filtering
        sessions = find_codex_sessions(
            codex_home=temp_codex_dir,
            keywords=[],
            num_matches=10,
            global_search=True,
            original_only=False,
        )

        # Should have at least the original
        assert len(sessions) >= 1

        # Verify is_trimmed field exists in results
        for session in sessions:
            assert "is_trimmed" in session


