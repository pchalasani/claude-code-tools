"""Tests for session ID consistency in clone and trim operations.

When sessions are cloned or trimmed, the new file gets a new UUID in its
filename. The sessionId inside the file content MUST also be updated to
match the new filename UUID.

Bug reference: Display showed session ID from JSON content instead of
filename, causing mismatch like "2833918d" displayed but file was
"c3efd44a-fa51-4926-a888-451d435445cd.jsonl".
"""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def sample_claude_session(tmp_path: Path) -> Path:
    """Create a sample Claude session with sessionId in every line."""
    original_uuid = "original-1111-2222-3333-444455556666"
    session_file = tmp_path / f"{original_uuid}.jsonl"

    lines = [
        {
            "type": "user",
            "sessionId": original_uuid,
            "cwd": "/Users/test/project",
            "message": {"role": "user", "content": "Hello"},
        },
        {
            "type": "assistant",
            "sessionId": original_uuid,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi there!"}],
            },
        },
        {
            "type": "user",
            "sessionId": original_uuid,
            "message": {"role": "user", "content": "Help me with Python"},
        },
        {
            "type": "assistant",
            "sessionId": original_uuid,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Sure, I can help!"}],
            },
        },
    ]

    with open(session_file, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    return session_file


@pytest.fixture
def sample_codex_session(tmp_path: Path) -> Path:
    """Create a sample Codex session with session ID in session_meta payload."""
    original_uuid = "original-aaaa-bbbb-cccc-ddddeeeeffff"
    session_file = tmp_path / f"rollout-2025-01-01T00-00-00-{original_uuid}.jsonl"

    lines = [
        {
            "type": "session_meta",
            "payload": {
                "id": original_uuid,
                "model": "gpt-4",
                "created_at": "2025-01-01T00:00:00Z",
            },
        },
        {
            "type": "message",
            "payload": {
                "role": "user",
                "content": "Hello Codex",
            },
        },
        {
            "type": "message",
            "payload": {
                "role": "assistant",
                "content": "Hi! How can I help?",
            },
        },
    ]

    with open(session_file, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    return session_file


def get_all_session_ids_claude(file_path: Path) -> list:
    """Extract all sessionId values from a Claude session file."""
    session_ids = []
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if line:
                data = json.loads(line)
                if "sessionId" in data:
                    session_ids.append(data["sessionId"])
    return session_ids


def get_codex_session_id(file_path: Path) -> str | None:
    """Extract session ID from Codex session_meta event."""
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if line:
                data = json.loads(line)
                if data.get("type") == "session_meta":
                    return data.get("payload", {}).get("id")
    return None


class TestClaudeCloneSessionId:
    """Tests for Claude session cloning with correct session ID."""

    def test_clone_updates_session_id_in_all_lines(
        self, sample_claude_session: Path, tmp_path: Path
    ):
        """
        When cloning a Claude session, the sessionId in every line of the
        new file must match the new filename UUID.
        """
        from claude_code_tools.trim_session import update_session_id_in_file
        import shutil
        import uuid

        # Simulate clone: copy file with new UUID filename
        new_uuid = str(uuid.uuid4())
        cloned_file = tmp_path / f"{new_uuid}.jsonl"
        shutil.copy2(sample_claude_session, cloned_file)

        # Update session IDs in the cloned file
        update_session_id_in_file(cloned_file, new_uuid, agent="claude")

        # Verify all sessionIds in the file match the new UUID
        session_ids = get_all_session_ids_claude(cloned_file)

        assert len(session_ids) > 0, "File should have lines with sessionId"
        assert all(sid == new_uuid for sid in session_ids), (
            f"All sessionIds should be '{new_uuid}', but found: {set(session_ids)}"
        )


class TestCodexCloneSessionId:
    """Tests for Codex session cloning with correct session ID."""

    def test_clone_updates_session_id_in_session_meta(
        self, sample_codex_session: Path, tmp_path: Path
    ):
        """
        When cloning a Codex session, the session ID in session_meta payload
        must match the new filename UUID.
        """
        from claude_code_tools.trim_session import update_session_id_in_file
        import shutil
        import uuid

        # Simulate clone: copy file with new UUID filename
        new_uuid = str(uuid.uuid4())
        cloned_file = tmp_path / f"rollout-2025-01-01T00-00-00-{new_uuid}.jsonl"
        shutil.copy2(sample_codex_session, cloned_file)

        # Update session ID in the cloned file
        update_session_id_in_file(cloned_file, new_uuid, agent="codex")

        # Verify session_meta ID matches the new UUID
        session_id = get_codex_session_id(cloned_file)

        assert session_id == new_uuid, (
            f"session_meta.payload.id should be '{new_uuid}', got '{session_id}'"
        )


class TestSmartTrimSessionId:
    """Tests for smart-trim updating session ID correctly."""

    def test_smart_trim_updates_session_id_claude(
        self, sample_claude_session: Path, tmp_path: Path
    ):
        """
        Smart-trim creates a new file with new UUID. All sessionIds in the
        output file must match the new filename UUID.
        """
        from claude_code_tools.smart_trim import trim_lines
        from claude_code_tools.trim_session import update_session_id_in_file
        import uuid

        # Create output file with new UUID
        new_uuid = str(uuid.uuid4())
        output_file = tmp_path / f"{new_uuid}.jsonl"

        # Trim some lines (trim line indices 1 and 3 - the assistant messages)
        trim_lines(sample_claude_session, [1, 3], output_file)

        # Update session IDs (this is what the fix should do)
        update_session_id_in_file(output_file, new_uuid, agent="claude")

        # Verify all sessionIds match the new UUID
        session_ids = get_all_session_ids_claude(output_file)

        assert len(session_ids) > 0, "Trimmed file should have lines with sessionId"
        assert all(sid == new_uuid for sid in session_ids), (
            f"All sessionIds should be '{new_uuid}', but found: {set(session_ids)}"
        )

    def test_smart_trim_updates_session_id_codex(
        self, sample_codex_session: Path, tmp_path: Path
    ):
        """
        Smart-trim on Codex creates a new file with new UUID. The session ID
        in session_meta must match the new filename UUID.
        """
        from claude_code_tools.smart_trim import trim_lines
        from claude_code_tools.trim_session import update_session_id_in_file
        import uuid

        # Create output file with new UUID
        new_uuid = str(uuid.uuid4())
        output_file = tmp_path / f"smart-trim-2025-01-01T00-00-00-{new_uuid}.jsonl"

        # Trim line index 2 (the assistant message)
        trim_lines(sample_codex_session, [2], output_file)

        # Update session ID (this is what the fix should do)
        update_session_id_in_file(output_file, new_uuid, agent="codex")

        # Verify session_meta ID matches the new UUID
        session_id = get_codex_session_id(output_file)

        assert session_id == new_uuid, (
            f"session_meta.payload.id should be '{new_uuid}', got '{session_id}'"
        )


class TestRegularTrimSessionId:
    """Tests that regular trim (which should already work) updates session ID."""

    def test_regular_trim_updates_session_id_claude(
        self, sample_claude_session: Path, tmp_path: Path
    ):
        """
        Regular trim_and_create_session should update sessionId in all lines.
        This is the control test - it should already pass.
        """
        from claude_code_tools.trim_session import trim_and_create_session

        result = trim_and_create_session(
            agent="claude",
            input_file=sample_claude_session,
            target_tools=None,  # Trim all
            threshold=10,  # Low threshold
            output_dir=tmp_path,
            min_token_savings=0,
        )

        output_file = result["output_file"]
        new_session_id = result["session_id"]

        # Verify all sessionIds in the file match the new UUID
        session_ids = get_all_session_ids_claude(output_file)

        assert len(session_ids) > 0, "Trimmed file should have lines with sessionId"
        assert all(sid == new_session_id for sid in session_ids), (
            f"All sessionIds should be '{new_session_id}', "
            f"but found: {set(session_ids)}"
        )


