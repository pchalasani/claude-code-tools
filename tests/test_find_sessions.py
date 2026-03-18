"""Unit tests for find_original_session and find_trimmed_sessions."""

import json
import tempfile
from pathlib import Path

import pytest

from claude_code_tools.find_original_session import find_original_session
from claude_code_tools.find_trimmed_sessions import (
    find_all_descendants,
    find_direct_children,
    get_search_dirs,
)
from claude_code_tools.trim_session import trim_and_create_session


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
def temp_session_dir():
    """Create a temporary directory structure for sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestFindOriginalSession:
    """Tests for finding the original session."""

    def test_original_session_returns_self(self, claude_session):
        """Test that an untrimmed session returns itself."""
        original = find_original_session(claude_session)
        assert original == claude_session

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_finds_original_after_one_trim(
        self, session_fixture, agent, temp_session_dir, request
    ):
        """Test finding original after one level of trimming."""
        original = request.getfixturevalue(session_fixture)

        # Create trimmed version
        result = trim_and_create_session(
            agent=agent,
            input_file=original,
            target_tools=None,
            threshold=500,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )

        trimmed = Path(result["output_file"])

        # Find original from trimmed
        found_original = find_original_session(trimmed)
        assert found_original == original

    @pytest.mark.parametrize(
        "session_fixture,agent",
        [
            ("claude_session", "claude"),
            ("codex_session", "codex"),
        ],
    )
    def test_finds_original_after_multiple_trims(
        self, session_fixture, agent, temp_session_dir, request
    ):
        """Test finding original after multiple levels of trimming."""
        original = request.getfixturevalue(session_fixture)

        # Create first trim
        result1 = trim_and_create_session(
            agent=agent,
            input_file=original,
            target_tools=None,
            threshold=500,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim1 = Path(result1["output_file"])

        # Create second trim (trim the trimmed file)
        result2 = trim_and_create_session(
            agent=agent,
            input_file=trim1,
            target_tools=None,
            threshold=400,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim2 = Path(result2["output_file"])

        # Create third trim
        result3 = trim_and_create_session(
            agent=agent,
            input_file=trim2,
            target_tools=None,
            threshold=300,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim3 = Path(result3["output_file"])

        # Find original from deepest trim
        found_original = find_original_session(trim3)
        assert found_original == original

    def test_raises_error_for_missing_file(self):
        """Test that FileNotFoundError is raised for missing files."""
        nonexistent = Path("/nonexistent/session.jsonl")
        with pytest.raises(FileNotFoundError):
            find_original_session(nonexistent)


class TestFindDirectChildren:
    """Tests for finding direct children of a session."""

    def test_finds_direct_children(
        self, claude_session, temp_session_dir
    ):
        """Test finding direct children of a session."""
        # Create two trimmed versions
        result1 = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )

        result2 = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=400,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )

        # Find children
        children = find_direct_children(
            claude_session, [temp_session_dir]
        )

        assert len(children) == 2
        assert Path(result1["output_file"]) in children
        assert Path(result2["output_file"]) in children

    def test_finds_only_direct_children(
        self, claude_session, temp_session_dir
    ):
        """Test that only direct children are found, not grandchildren."""
        # Create parent -> child -> grandchild chain
        result1 = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        child = Path(result1["output_file"])

        result2 = trim_and_create_session(
            agent="claude",
            input_file=child,
            target_tools=None,
            threshold=400,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        grandchild = Path(result2["output_file"])

        # Find direct children of original
        children = find_direct_children(
            claude_session, [temp_session_dir]
        )

        assert child in children
        assert grandchild not in children

    def test_no_children_returns_empty(self, claude_session):
        """Test that finding children of untrimmed session returns empty."""
        children = find_direct_children(
            claude_session, [Path("/nonexistent")]
        )
        assert children == []


class TestFindAllDescendants:
    """Tests for finding all descendants recursively."""

    def test_finds_all_descendants(
        self, claude_session, temp_session_dir
    ):
        """Test finding all descendants in a multi-level tree."""
        # Create tree:
        #   original
        #   ├── trim1
        #   │   └── trim1a
        #   └── trim2

        result1 = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim1 = Path(result1["output_file"])

        result1a = trim_and_create_session(
            agent="claude",
            input_file=trim1,
            target_tools=None,
            threshold=400,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim1a = Path(result1a["output_file"])

        result2 = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=450,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim2 = Path(result2["output_file"])

        # Find all descendants
        lineage = find_all_descendants(
            claude_session, [temp_session_dir]
        )

        # Check structure
        assert claude_session in lineage
        assert trim1 in lineage
        assert trim2 in lineage
        assert trim1a in lineage

        # Check relationships
        assert trim1 in lineage[claude_session]
        assert trim2 in lineage[claude_session]
        assert trim1a in lineage[trim1]

    def test_empty_tree_for_untrimmed(
        self, claude_session, temp_session_dir
    ):
        """Test that untrimmed session with no children returns minimal tree."""
        lineage = find_all_descendants(
            claude_session, [temp_session_dir]
        )

        assert claude_session in lineage
        assert lineage[claude_session] == []


class TestGetSearchDirs:
    """Tests for getting search directories."""

    def test_returns_custom_dir_when_provided(self):
        """Test that custom directory is returned when provided."""
        custom = Path("/custom/dir")
        dirs = get_search_dirs(custom)
        assert dirs == [custom]

    def test_returns_default_dirs_when_none(self):
        """Test that default directories are returned when none provided."""
        dirs = get_search_dirs(None)
        assert len(dirs) == 2
        assert any("claude" in str(d) for d in dirs)
        assert any("codex" in str(d) for d in dirs)


class TestTrimMetadataPreservation:
    """Tests for metadata preservation across multiple trims."""

    def test_metadata_updated_on_retrim(
        self, claude_session, temp_session_dir
    ):
        """Test that metadata is updated when trimming a trimmed session."""
        # First trim
        result1 = trim_and_create_session(
            agent="claude",
            input_file=claude_session,
            target_tools=None,
            threshold=500,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim1 = Path(result1["output_file"])

        # Second trim
        result2 = trim_and_create_session(
            agent="claude",
            input_file=trim1,
            target_tools=None,
            threshold=400,
            output_dir=temp_session_dir,
            min_token_savings=0,
        )
        trim2 = Path(result2["output_file"])

        # Check that trim2's metadata points to trim1, not original
        with open(trim2) as f:
            first_line = json.loads(f.readline())
            assert "trim_metadata" in first_line
            assert first_line["trim_metadata"]["parent_file"] == str(
                trim1.absolute()
            )
            # Should not point to original
            assert first_line["trim_metadata"]["parent_file"] != str(
                claude_session.absolute()
            )
