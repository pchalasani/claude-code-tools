"""Tests for in-place session trimming (library + click CLI).

``trim_session_in_place`` mutates files in place, so every test copies
a fixture (or builds a synthetic session) inside ``tmp_path`` and
operates only on that copy.
"""

import json
import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from click.testing import CliRunner

import claude_code_tools.trim_in_place as trim_in_place_module
from claude_code_tools.aichat import trim_in_place_cmd
from claude_code_tools.trim_in_place import trim_session_in_place

FIXTURES_DIR = Path(__file__).parent / "fixtures"

SESSION_ID = "sess-trim-in-place"

BACKUP_NAME_RE = re.compile(
    r"^(?P<stem>.+)\.pre-trim-\d{8}-\d{6}(-\d+)?\.jsonl\.bak$"
)

RESULT_KEYS = {
    "applied",
    "dry_run",
    "nothing_to_trim",
    "num_tools_trimmed",
    "num_assistant_trimmed",
    "chars_saved",
    "tokens_saved",
    "backup_file",
    "session_file",
    "size_before",
    "size_after",
}

# Long payloads (well over the default 500-char threshold) with unique
# markers so tests can tell trimmed content from surviving content.
READ_RESULT = "UNIQ-READ-RESULT " + ("r" * 6400)
BASH_RESULT = "UNIQ-BASH-RESULT " + ("b" * 6400)
ASST_ONE = "UNIQ-ASST-ONE " + ("alpha " * 700)
ASST_TWO = "UNIQ-ASST-TWO " + ("beta " * 800)
ASST_THREE = "UNIQ-ASST-THREE " + ("gamma " * 700)


def _entry(idx: int, etype: str, message: Dict[str, Any]) -> Dict[str, Any]:
    """Build one synthetic session line with identity fields."""
    return {
        "type": etype,
        "sessionId": SESSION_ID,
        "uuid": f"u{idx}",
        "parentUuid": None if idx == 1 else f"u{idx - 1}",
        "message": message,
    }


def make_synthetic_session(path: Path) -> Path:
    """Write a 10-line Claude session with 2 long tool results and
    3 long assistant messages (plus short filler), returning the path.
    """
    entries = [
        _entry(
            1,
            "user",
            {
                "role": "user",
                "content": [{"type": "text", "text": "Please read the file"}],
            },
        ),
        _entry(
            2,
            "assistant",
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool1",
                        "name": "Read",
                        "input": {"file_path": "/test/file.txt"},
                    }
                ],
            },
        ),
        _entry(
            3,
            "user",
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool1",
                        "content": READ_RESULT,
                    }
                ],
            },
        ),
        _entry(
            4,
            "assistant",
            {
                "role": "assistant",
                "content": [{"type": "text", "text": ASST_ONE}],
            },
        ),
        _entry(
            5,
            "user",
            {
                "role": "user",
                "content": [{"type": "text", "text": "Now run a command"}],
            },
        ),
        _entry(
            6,
            "assistant",
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool2",
                        "name": "Bash",
                        "input": {"command": "ls -la"},
                    }
                ],
            },
        ),
        _entry(
            7,
            "user",
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool2",
                        "content": BASH_RESULT,
                    }
                ],
            },
        ),
        _entry(
            8,
            "assistant",
            {
                "role": "assistant",
                "content": [{"type": "text", "text": ASST_TWO}],
            },
        ),
        _entry(
            9,
            "assistant",
            {
                "role": "assistant",
                "content": [{"type": "text", "text": ASST_THREE}],
            },
        ),
        _entry(
            10,
            "assistant",
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Short reply"}],
            },
        ),
    ]
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def read_records(path: Path) -> List[Dict[str, Any]]:
    """Parse every JSONL line of a session file."""
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def tool_result_content(
    records: List[Dict[str, Any]], tool_use_id: str
) -> Optional[str]:
    """Return the tool_result content for the given tool_use_id."""
    for rec in records:
        content = rec.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_result"
                and item.get("tool_use_id") == tool_use_id
            ):
                return item.get("content")
    return None


def assistant_texts(records: List[Dict[str, Any]]) -> List[str]:
    """Return all assistant text blocks, in file order."""
    texts: List[str] = []
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        content = rec.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
    return texts


def dir_names(path: Path) -> List[str]:
    """Sorted names of all entries in a directory."""
    return sorted(p.name for p in path.iterdir())


def single_json_line(output: str) -> Dict[str, Any]:
    """Assert output is EXACTLY one non-empty line; parse it as JSON.

    The hook consumes this CLI programmatically, so both success and
    error paths must emit pure single-line JSON on stdout (no banners,
    warnings or any other text around it).
    """
    lines = [ln for ln in output.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line: {output!r}"
    return json.loads(lines[0])


def append_entry(uuid: str, text: str) -> Dict[str, Any]:
    """A short user entry suitable for simulating a concurrent append."""
    return {
        "type": "user",
        "sessionId": SESSION_ID,
        "uuid": uuid,
        "parentUuid": "u10",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


@pytest.fixture
def synthetic_session(tmp_path: Path) -> Path:
    """A synthetic trimmable Claude session inside tmp_path."""
    return make_synthetic_session(tmp_path / "synthetic.jsonl")


@pytest.fixture
def claude_copy(tmp_path: Path) -> Path:
    """A tmp copy of the small Claude fixture (safe to mutate)."""
    dst = tmp_path / "claude_session.jsonl"
    shutil.copy2(FIXTURES_DIR / "claude_session.jsonl", dst)
    return dst


@pytest.fixture
def codex_copy(tmp_path: Path) -> Path:
    """A tmp copy of the Codex fixture (safe to mutate)."""
    dst = tmp_path / "codex_session.jsonl"
    shutil.copy2(FIXTURES_DIR / "codex_session.jsonl", dst)
    return dst


class TestDryRun:
    """Dry runs must report savings without touching anything."""

    def test_stats_and_file_untouched(
        self, synthetic_session: Path, tmp_path: Path
    ) -> None:
        """Dry run reports savings, leaves file/dir byte-identical."""
        original_bytes = synthetic_session.read_bytes()

        result = trim_session_in_place(synthetic_session, dry_run=True)

        assert set(result.keys()) == RESULT_KEYS
        assert result["dry_run"] is True
        assert result["applied"] is False
        assert result["nothing_to_trim"] is False
        assert result["num_tools_trimmed"] == 2
        assert result["num_assistant_trimmed"] == 0
        assert result["chars_saved"] > 0
        assert result["tokens_saved"] > 0
        assert result["tokens_saved"] == result["chars_saved"] // 4
        assert result["backup_file"] is None
        assert result["session_file"] == str(synthetic_session)
        assert result["size_before"] == len(original_bytes)
        assert result["size_after"] < result["size_before"]

        # File is byte-identical; no backup, no leftover temp files.
        assert synthetic_session.read_bytes() == original_bytes
        assert dir_names(tmp_path) == [synthetic_session.name]


class TestApply:
    """Real applies swap in trimmed content and keep a faithful backup."""

    def test_apply_trims_and_creates_backup(
        self, synthetic_session: Path, tmp_path: Path
    ) -> None:
        """Apply rewrites the file, backs up the original untouched."""
        original_bytes = synthetic_session.read_bytes()
        original_records = read_records(synthetic_session)

        result = trim_session_in_place(synthetic_session)

        assert result["applied"] is True
        assert result["dry_run"] is False
        assert result["nothing_to_trim"] is False
        assert result["num_tools_trimmed"] == 2
        assert result["tokens_saved"] > 0

        final_bytes = synthetic_session.read_bytes()
        assert final_bytes != original_bytes
        assert result["size_before"] == len(original_bytes)
        assert result["size_after"] == len(final_bytes)
        assert len(final_bytes) < len(original_bytes)

        # Backup: exists, name pattern, byte-identical to the original.
        assert result["backup_file"] is not None
        backup = Path(result["backup_file"])
        assert backup.parent == synthetic_session.parent
        assert backup.exists()
        match = BACKUP_NAME_RE.match(backup.name)
        assert match is not None, backup.name
        assert match.group("stem") == synthetic_session.stem
        assert backup.read_bytes() == original_bytes

        # No temp files left behind: exactly session + backup.
        assert dir_names(tmp_path) == sorted(
            [synthetic_session.name, backup.name]
        )

        # Placeholders in the trimmed file reference the backup path.
        final_text = synthetic_session.read_text()
        assert str(backup) in final_text

        # Line count and identity fields are preserved per line.
        trimmed_records = read_records(synthetic_session)
        backup_records = read_records(backup)
        assert len(trimmed_records) == len(original_records)
        assert len(backup_records) == len(original_records)
        for trimmed, original in zip(trimmed_records, backup_records):
            for key in ("sessionId", "uuid", "parentUuid"):
                assert trimmed.get(key) == original.get(key)

        # Both long tool results were truncated (prefix retained).
        for tool_id, payload in (
            ("tool1", READ_RESULT),
            ("tool2", BASH_RESULT),
        ):
            content = tool_result_content(trimmed_records, tool_id)
            assert content is not None
            assert content != payload
            assert content.startswith(payload[:500])
            assert "...truncated" in content

    def test_apply_preserves_file_mode(
        self, synthetic_session: Path
    ) -> None:
        """The swapped-in file keeps the original permissions."""
        synthetic_session.chmod(0o640)

        result = trim_session_in_place(synthetic_session)

        assert result["applied"] is True
        mode = stat.S_IMODE(synthetic_session.stat().st_mode)
        assert mode == 0o640

    def test_apply_on_fixture_copy(self, claude_copy: Path) -> None:
        """The real fixture trims in place with identity preserved."""
        original_records = read_records(claude_copy)

        result = trim_session_in_place(
            claude_copy, threshold=100, min_token_savings=0
        )

        assert result["applied"] is True
        assert result["num_tools_trimmed"] >= 1
        backup = Path(result["backup_file"])
        assert backup.exists()

        trimmed_records = read_records(claude_copy)
        assert len(trimmed_records) == len(original_records) == 9
        assert trimmed_records[0]["sessionId"] == "abc123"
        assert "...truncated" in claude_copy.read_text()


class TestSecondApply:
    """A re-run on an already-trimmed file must be a no-op."""

    def test_second_apply_is_noop(
        self, synthetic_session: Path, tmp_path: Path
    ) -> None:
        """Second apply: nothing_to_trim, file untouched, one backup."""
        first = trim_session_in_place(synthetic_session)
        assert first["applied"] is True
        after_first_bytes = synthetic_session.read_bytes()
        backups = list(tmp_path.glob("*.bak"))
        assert len(backups) == 1

        second = trim_session_in_place(synthetic_session)

        assert second["nothing_to_trim"] is True
        assert second["applied"] is False
        assert second["backup_file"] is None
        assert synthetic_session.read_bytes() == after_first_bytes
        assert list(tmp_path.glob("*.bak")) == backups
        assert dir_names(tmp_path) == sorted(
            [synthetic_session.name, backups[0].name]
        )


class TestRepeatedStricterTrim:
    """A re-trim with a stricter threshold must never re-truncate the
    placeholders minted by an earlier trim: doing so would replace the
    reference to the ORIGINAL backup (the only path to the full
    content) with a reference to a new backup that only contains the
    placeholder itself."""

    def test_tool_placeholders_survive_stricter_retrim(
        self, synthetic_session: Path
    ) -> None:
        """Trim at 500 then re-trim at 100 (min_token_savings=0): the
        second run trims nothing and the first-backup references
        survive byte-for-byte."""
        first = trim_session_in_place(synthetic_session, threshold=500)
        assert first["applied"] is True
        assert first["num_tools_trimmed"] == 2
        first_backup = Path(first["backup_file"])
        after_first = synthetic_session.read_bytes()

        second = trim_session_in_place(
            synthetic_session, threshold=100, min_token_savings=0
        )

        # Nothing was re-trimmed; the session is byte-identical.
        assert second["num_tools_trimmed"] == 0
        assert second["num_assistant_trimmed"] == 0
        assert second["chars_saved"] == 0
        assert synthetic_session.read_bytes() == after_first

        # min_token_savings=0 lets the no-op apply proceed; its backup
        # is a copy of the ALREADY-trimmed session, and no placeholder
        # may reference it.
        assert second["applied"] is True
        second_backup = Path(second["backup_file"])
        assert second_backup.read_bytes() == after_first
        final_text = synthetic_session.read_text()
        assert str(first_backup) in final_text
        assert str(second_backup) not in final_text

        # The kept prefix is still the first 500 chars of the ORIGINAL
        # payload - not re-cut down to 100.
        records = read_records(synthetic_session)
        for tool_id, payload in (
            ("tool1", READ_RESULT),
            ("tool2", BASH_RESULT),
        ):
            content = tool_result_content(records, tool_id)
            assert content is not None
            assert content.startswith(payload[:500])

    def test_assistant_placeholders_survive_stricter_retrim(
        self, synthetic_session: Path
    ) -> None:
        """Assistant placeholders from a first trim keep referencing
        the first backup; only not-yet-trimmed long messages are
        trimmed by the stricter second run."""
        first = trim_session_in_place(
            synthetic_session,
            target_tools={"nosuchtool"},  # leave tool results alone
            trim_assistant_messages=2,
        )
        assert first["applied"] is True
        assert first["num_assistant_trimmed"] == 2
        first_backup = str(first["backup_file"])

        second = trim_session_in_place(
            synthetic_session,
            target_tools={"nosuchtool"},
            trim_assistant_messages=2,
            threshold=100,
            min_token_savings=0,
        )

        assert second["applied"] is True
        # Only ASST_THREE (untouched by the first trim) is trimmed;
        # the two existing placeholders do not occupy trim slots and
        # are not replaced again.
        assert second["num_assistant_trimmed"] == 1

        texts = assistant_texts(read_records(synthetic_session))
        placeholders = [
            t for t in texts if t.startswith("[Assistant message trimmed")
        ]
        assert len(placeholders) == 3
        # First-trim placeholders still reference the FIRST backup...
        assert sum(first_backup in t for t in placeholders) == 2
        # ...and the newly trimmed message references the second one.
        second_backup = str(second["backup_file"])
        assert sum(second_backup in t for t in placeholders) == 1


class TestMinTokenSavings:
    """min_token_savings gates whether the trim is worth applying."""

    def test_huge_min_forces_nothing_to_trim(
        self, synthetic_session: Path, tmp_path: Path
    ) -> None:
        """A huge min blocks a real apply on a trimmable file."""
        original_bytes = synthetic_session.read_bytes()

        result = trim_session_in_place(
            synthetic_session, min_token_savings=10**9
        )

        assert result["nothing_to_trim"] is True
        assert result["applied"] is False
        assert result["backup_file"] is None
        # It WAS trimmable — just below the (absurd) bar.
        assert result["tokens_saved"] > 0
        assert synthetic_session.read_bytes() == original_bytes
        assert dir_names(tmp_path) == [synthetic_session.name]


class TestAssistantTrimming:
    """trim_assistant_messages: positive and negative specs."""

    @pytest.mark.parametrize(
        "spec,trimmed_markers,kept_markers",
        [
            (1, ["UNIQ-ASST-ONE"], ["UNIQ-ASST-TWO", "UNIQ-ASST-THREE"]),
            (
                2,
                ["UNIQ-ASST-ONE", "UNIQ-ASST-TWO"],
                ["UNIQ-ASST-THREE"],
            ),
            (
                -1,
                ["UNIQ-ASST-ONE", "UNIQ-ASST-TWO"],
                ["UNIQ-ASST-THREE"],
            ),
            (-2, ["UNIQ-ASST-ONE"], ["UNIQ-ASST-TWO", "UNIQ-ASST-THREE"]),
        ],
    )
    def test_assistant_specs(
        self,
        synthetic_session: Path,
        spec: int,
        trimmed_markers: List[str],
        kept_markers: List[str],
    ) -> None:
        """Positive N trims first N; negative N keeps the last |N|."""
        result = trim_session_in_place(
            synthetic_session,
            target_tools={"nosuchtool"},  # leave tool results alone
            trim_assistant_messages=spec,
        )

        assert result["applied"] is True
        assert result["num_assistant_trimmed"] == len(trimmed_markers)
        assert result["num_tools_trimmed"] == 0

        records = read_records(synthetic_session)
        texts = assistant_texts(records)
        joined = "\n".join(texts)
        for marker in trimmed_markers:
            assert marker not in joined
        for marker in kept_markers:
            assert marker in joined
        placeholders = [
            t for t in texts if t.startswith("[Assistant message trimmed")
        ]
        assert len(placeholders) == len(trimmed_markers)

        # Tool results were excluded from trimming and survive intact.
        assert tool_result_content(records, "tool1") == READ_RESULT
        assert tool_result_content(records, "tool2") == BASH_RESULT


class TestToolFilteringAndThreshold:
    """target_tools selects tools; threshold sets the kept prefix."""

    def test_target_tools_trims_only_named_tool(
        self, synthetic_session: Path
    ) -> None:
        """Only the Read result is trimmed; Bash survives intact."""
        result = trim_session_in_place(
            synthetic_session, target_tools={"read"}
        )

        assert result["applied"] is True
        assert result["num_tools_trimmed"] == 1

        records = read_records(synthetic_session)
        read_content = tool_result_content(records, "tool1")
        bash_content = tool_result_content(records, "tool2")
        assert bash_content == BASH_RESULT
        assert read_content != READ_RESULT
        assert read_content.startswith(READ_RESULT[:500])
        assert "...truncated" in read_content
        assert result["backup_file"] in read_content

    def test_threshold_sets_kept_prefix(
        self, synthetic_session: Path
    ) -> None:
        """threshold=1000 keeps exactly the first 1000 characters."""
        result = trim_session_in_place(synthetic_session, threshold=1000)

        assert result["applied"] is True
        records = read_records(synthetic_session)
        read_content = tool_result_content(records, "tool1")
        assert read_content.startswith(READ_RESULT[:1000])
        assert not read_content.startswith(READ_RESULT[:1001])
        assert "...truncated" in read_content

    def test_higher_threshold_saves_less(
        self, synthetic_session: Path
    ) -> None:
        """Raising the threshold shrinks the savings (dry runs)."""
        low = trim_session_in_place(
            synthetic_session, threshold=500, dry_run=True
        )
        high = trim_session_in_place(
            synthetic_session, threshold=2000, dry_run=True
        )

        assert low["chars_saved"] > high["chars_saved"] > 0


class TestErrors:
    """Input validation errors."""

    def test_codex_session_raises_value_error(
        self, codex_copy: Path
    ) -> None:
        """Non-Claude (Codex) sessions are rejected untouched."""
        original_bytes = codex_copy.read_bytes()
        with pytest.raises(ValueError, match="codex"):
            trim_session_in_place(codex_copy)
        assert codex_copy.read_bytes() == original_bytes

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """A nonexistent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            trim_session_in_place(tmp_path / "no_such_session.jsonl")


class TestInvalidUtf8Passthrough:
    """Lines carrying invalid UTF-8 round-trip byte-for-byte."""

    def test_parseable_line_with_invalid_utf8_round_trips(
        self, synthetic_session: Path
    ) -> None:
        """A line that parses as Claude JSON but contains an invalid
        UTF-8 byte is passed through verbatim by a real apply - never
        re-encoded as a \\udcff escape - while the rest of the session
        still trims."""
        hostile = (
            b'{"type":"user","sessionId":"sess-trim-in-place",'
            b'"uuid":"u-bad-utf8","parentUuid":"u10",'
            b'"message":{"role":"user","content":"raw \xff bytes"}}\n'
        )
        with open(synthetic_session, "ab") as f:
            f.write(hostile)

        result = trim_session_in_place(synthetic_session)

        assert result["applied"] is True
        assert result["num_tools_trimmed"] == 2  # trim still effective
        final = synthetic_session.read_bytes()
        assert hostile in final  # byte-for-byte, 0xff intact
        assert b"\\udcff" not in final
        # The backup holds the original bytes too.
        backup = Path(result["backup_file"])
        assert hostile in backup.read_bytes()


class TestConcurrentAppendGuard:
    """The stat-signature guard retries when the file grows mid-trim."""

    def test_append_during_trim_is_not_lost(
        self,
        synthetic_session: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A line appended mid-trim survives via the retry loop."""
        appended_entry = {
            "type": "user",
            "sessionId": SESSION_ID,
            "uuid": "u-appended",
            "parentUuid": "u10",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "CONCURRENT-APPEND-MARKER"}
                ],
            },
        }
        original_line_count = len(read_records(synthetic_session))
        real_backup = trim_in_place_module._backup_original
        calls = {"n": 0}

        def racing_backup(src: Any, dst: Any, ino: Any) -> Any:
            """Append to the session right before the first backup."""
            calls["n"] += 1
            if calls["n"] == 1:
                with open(src, "a") as f:
                    f.write(json.dumps(appended_entry) + "\n")
            return real_backup(src, dst, ino)

        monkeypatch.setattr(
            trim_in_place_module, "_backup_original", racing_backup
        )

        result = trim_session_in_place(synthetic_session)

        assert result["applied"] is True
        assert calls["n"] == 2, "expected one retry after the append"

        # The appended line made it into the final trimmed file.
        records = read_records(synthetic_session)
        assert len(records) == original_line_count + 1
        assert records[-1]["uuid"] == "u-appended"
        assert "CONCURRENT-APPEND-MARKER" in synthetic_session.read_text()

        # The surviving backup also includes the appended line.
        backup = Path(result["backup_file"])
        assert backup.exists()
        assert "CONCURRENT-APPEND-MARKER" in backup.read_text()

    def test_append_in_final_replace_window_is_preserved(
        self,
        synthetic_session: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A line appended AFTER the final stat check but BEFORE
        os.replace (the last TOCTOU window) survives the swap.

        The append lands on the old inode; the held fd on that inode
        lets the trim merge it onto the swapped-in file and backup.
        """
        line = (
            json.dumps(
                append_entry("u-replace-window", "REPLACE-WINDOW-MARKER")
            )
            + "\n"
        )
        original_line_count = len(read_records(synthetic_session))
        real_replace = os.replace
        fired = {"n": 0}

        def racing_replace(src: Any, dst: Any) -> Any:
            """Append to the live session right before the swap."""
            if Path(dst) == synthetic_session and fired["n"] == 0:
                fired["n"] = 1
                with open(dst, "a") as f:
                    f.write(line)
            return real_replace(src, dst)

        monkeypatch.setattr(
            trim_in_place_module.os, "replace", racing_replace
        )

        result = trim_session_in_place(synthetic_session)

        assert fired["n"] == 1
        assert result["applied"] is True

        # The window append made it into the final trimmed file.
        final_text = synthetic_session.read_text()
        assert "REPLACE-WINDOW-MARKER" in final_text
        assert "...truncated" in final_text  # trim still applied
        records = read_records(synthetic_session)
        assert len(records) == original_line_count + 1
        assert records[-1]["uuid"] == "u-replace-window"
        # The reported size reflects the merged bytes on disk.
        assert result["size_after"] == synthetic_session.stat().st_size

        # The backup preserves it too (it is a faithful superset).
        backup = Path(result["backup_file"])
        assert "REPLACE-WINDOW-MARKER" in backup.read_text()

    def test_interleaved_appends_keep_chronological_order(
        self,
        synthetic_session: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Line A appended in the replace window (old inode) followed
        by line B appended by path right after the swap (new inode)
        must end up as trimmed-lines, A, B - never B, A - so the
        uuid/parentUuid chain stays valid.
        """
        line_a = (
            json.dumps(append_entry("u-a", "WINDOW-APPEND-A")) + "\n"
        )
        entry_b = {
            "type": "user",
            "sessionId": SESSION_ID,
            "uuid": "u-b",
            "parentUuid": "u-a",  # B chains onto A, not onto u10
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "POST-SWAP-B"}],
            },
        }
        line_b = json.dumps(entry_b) + "\n"
        original_line_count = len(read_records(synthetic_session))
        real_replace = os.replace
        fired = {"n": 0}

        def racing_replace(src: Any, dst: Any) -> Any:
            """A lands on the old inode, B on the new one."""
            if Path(dst) == synthetic_session and fired["n"] == 0:
                fired["n"] = 1
                with open(dst, "a") as f:  # pre-swap: old inode
                    f.write(line_a)
                rv = real_replace(src, dst)
                with open(dst, "a") as f:  # post-swap: new inode
                    f.write(line_b)
                return rv
            return real_replace(src, dst)

        monkeypatch.setattr(
            trim_in_place_module.os, "replace", racing_replace
        )

        result = trim_session_in_place(synthetic_session)

        assert fired["n"] == 1
        assert result["applied"] is True

        final_text = synthetic_session.read_text()
        assert "...truncated" in final_text  # trim still applied
        records = read_records(synthetic_session)
        assert len(records) == original_line_count + 2
        # Chronological order restored: A (older) before B (newer).
        assert [r["uuid"] for r in records[-2:]] == ["u-a", "u-b"]
        # The reported size covers BOTH appends: the spliced old-inode
        # line A and the by-path post-swap line B.
        assert result["size_after"] == synthetic_session.stat().st_size
        # The parentUuid chain is intact across the merge.
        assert records[-2]["parentUuid"] == "u10"
        assert records[-1]["parentUuid"] == "u-a"
        # A raced onto the old inode, so the backup mirrors it; B
        # landed on the live file only.
        backup = Path(result["backup_file"])
        assert "WINDOW-APPEND-A" in backup.read_text()

    def test_post_swap_by_path_append_reflected_in_size_after(
        self,
        synthetic_session: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A line appended BY PATH right after the swap (new inode,
        no old-inode race at all) must be counted in the reported
        size_after: the result promises the post-trim on-disk size,
        not the pre-swap temp file's size.
        """
        line_b = (
            json.dumps(append_entry("u-post-swap", "POST-SWAP-ONLY"))
            + "\n"
        )
        original_line_count = len(read_records(synthetic_session))
        real_replace = os.replace
        fired = {"n": 0}

        def racing_replace(src: Any, dst: Any) -> Any:
            """Append by path to the NEW inode right after the swap."""
            rv = real_replace(src, dst)
            if Path(dst) == synthetic_session and fired["n"] == 0:
                fired["n"] = 1
                with open(dst, "a") as f:  # post-swap: new inode
                    f.write(line_b)
            return rv

        monkeypatch.setattr(
            trim_in_place_module.os, "replace", racing_replace
        )

        result = trim_session_in_place(synthetic_session)

        assert fired["n"] == 1
        assert result["applied"] is True

        records = read_records(synthetic_session)
        assert len(records) == original_line_count + 1
        assert records[-1]["uuid"] == "u-post-swap"
        assert result["size_after"] == synthetic_session.stat().st_size

    def test_retry_exhaustion_raises_and_preserves_appends(
        self,
        synthetic_session: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the file changes on EVERY attempt, the trim gives up with
        RuntimeError, keeps every appended line, and leaves no
        .trim-tmp or .bak artifacts behind.
        """
        original_records = read_records(synthetic_session)
        real_backup = trim_in_place_module._backup_original
        calls = {"n": 0}

        def always_racing_backup(src: Any, dst: Any, ino: Any) -> Any:
            """Append a fresh line to the session on every attempt."""
            calls["n"] += 1
            entry = append_entry(
                f"u-race-{calls['n']}", f"RACE-MARKER-{calls['n']}"
            )
            with open(src, "a") as f:
                f.write(json.dumps(entry) + "\n")
            return real_backup(src, dst, ino)

        monkeypatch.setattr(
            trim_in_place_module, "_backup_original", always_racing_backup
        )

        with pytest.raises(RuntimeError, match="changed during trim"):
            trim_session_in_place(synthetic_session)

        max_attempts = trim_in_place_module.MAX_ATTEMPTS
        assert calls["n"] == max_attempts

        # Original content + every appended line survive, untrimmed.
        records = read_records(synthetic_session)
        assert len(records) == len(original_records) + max_attempts
        assert records[: len(original_records)] == original_records
        for i in range(1, max_attempts + 1):
            assert records[len(original_records) + i - 1] == append_entry(
                f"u-race-{i}", f"RACE-MARKER-{i}"
            )
        assert "...truncated" not in synthetic_session.read_text()

        # No temp files, no backups: only the session file remains.
        assert dir_names(tmp_path) == [synthetic_session.name]


class TestAppendBytesPartialWrites:
    """os.write may write fewer bytes than asked; _append_bytes must
    retry until the whole buffer is on disk (a single unchecked call
    would silently drop the tail of a merged session line)."""

    def test_short_writes_are_retried_until_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every partial write is resumed from the returned offset."""
        target = tmp_path / "target.jsonl"
        target.write_bytes(b"head\n")
        payload = b"PARTIAL-WRITE-" + b"x" * 300 + b"\n"
        real_write = os.write
        sizes: List[int] = []

        def short_write(fd: int, data: Any) -> int:
            """Write at most a third of what was asked."""
            buf = bytes(data)
            sizes.append(len(buf))
            return real_write(fd, buf[: max(1, len(buf) // 3)])

        monkeypatch.setattr(trim_in_place_module.os, "write", short_write)
        trim_in_place_module._append_bytes(target, payload)
        monkeypatch.undo()

        assert target.read_bytes() == b"head\n" + payload
        assert len(sizes) > 1, "retry loop never re-invoked os.write"

    def test_zero_byte_write_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A zero-byte write makes no progress and must raise, not
        spin forever."""
        target = tmp_path / "target.jsonl"
        target.write_bytes(b"")

        monkeypatch.setattr(
            trim_in_place_module.os, "write", lambda fd, data: 0
        )

        with pytest.raises(OSError, match="returned 0"):
            trim_in_place_module._append_bytes(target, b"payload")

    def test_partial_write_during_merge_keeps_full_line(
        self,
        synthetic_session: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A short os.write while merging a replace-window append must
        not drop the suffix of the merged line: the complete line ends
        up in both the live session and the backup."""
        marker = "SHORT-WRITE-MARKER-" + "z" * 200
        line = json.dumps(append_entry("u-short-write", marker)) + "\n"
        real_replace = os.replace
        fired = {"n": 0}

        def racing_replace(src: Any, dst: Any) -> Any:
            """Append to the live session right before the swap."""
            if Path(dst) == synthetic_session and fired["n"] == 0:
                fired["n"] = 1
                with open(dst, "a") as f:  # pre-swap: old inode
                    f.write(line)
            return real_replace(src, dst)

        real_write = os.write
        shortened = {"n": 0}

        def short_write(fd: int, data: Any) -> int:
            """Halve any write that carries the merged line."""
            buf = bytes(data)
            if b"SHORT-WRITE-MARKER" in buf and len(buf) > 1:
                shortened["n"] += 1
                return real_write(fd, buf[: len(buf) // 2])
            return real_write(fd, data)

        monkeypatch.setattr(
            trim_in_place_module.os, "replace", racing_replace
        )
        monkeypatch.setattr(trim_in_place_module.os, "write", short_write)

        result = trim_session_in_place(synthetic_session)

        assert fired["n"] == 1
        assert result["applied"] is True
        assert shortened["n"] >= 1, "short-write path never exercised"

        # The merged line survives COMPLETE (parses as one record) in
        # the live session and in the backup.
        records = read_records(synthetic_session)
        assert records[-1]["uuid"] == "u-short-write"
        assert marker in synthetic_session.read_text()
        backup = Path(result["backup_file"])
        backup_records = read_records(backup)
        assert backup_records[-1]["uuid"] == "u-short-write"
        assert marker in backup.read_text()


class TestBackupReservation:
    """Backup paths are reserved atomically; never overwritten."""

    def test_colliding_backup_name_is_not_overwritten(
        self,
        synthetic_session: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A pre-existing backup with the exact timestamped name keeps
        its contents; the trim reserves the next (-2) candidate.
        """
        frozen = datetime(2026, 7, 4, 8, 30, 12)

        class FrozenDateTime:
            """datetime stand-in whose now() is pinned."""

            @staticmethod
            def now() -> datetime:
                return frozen

        monkeypatch.setattr(
            trim_in_place_module, "datetime", FrozenDateTime
        )

        stem = synthetic_session.stem
        taken = tmp_path / f"{stem}.pre-trim-20260704-083012.jsonl.bak"
        sentinel = "PRE-EXISTING BACKUP - MUST NOT BE CLOBBERED\n"
        taken.write_text(sentinel)
        original_bytes = synthetic_session.read_bytes()

        result = trim_session_in_place(synthetic_session)

        assert result["applied"] is True
        backup = Path(result["backup_file"])
        assert (
            backup.name == f"{stem}.pre-trim-20260704-083012-2.jsonl.bak"
        )
        assert backup.read_bytes() == original_bytes
        # The colliding file survives byte-for-byte.
        assert taken.read_text() == sentinel

    def test_reserve_backup_path_creates_the_file(
        self, tmp_path: Path
    ) -> None:
        """Reservation CREATES the path (O_CREAT|O_EXCL), so a second
        reservation in the same second cannot pick the same name.
        """
        session = tmp_path / "abc.jsonl"
        session.write_text("{}\n")

        first = trim_in_place_module._reserve_backup_path(session)
        second = trim_in_place_module._reserve_backup_path(session)

        assert first.exists()
        assert second.exists()
        assert first != second
        assert BACKUP_NAME_RE.match(first.name)
        assert BACKUP_NAME_RE.match(second.name)


class TestThresholdValidation:
    """Nonsensical thresholds are rejected before any work happens."""

    @pytest.mark.parametrize("bad", [0, -1, -500])
    def test_library_rejects_nonpositive_threshold(
        self, synthetic_session: Path, tmp_path: Path, bad: int
    ) -> None:
        """threshold < 1 raises ValueError; nothing is touched."""
        original_bytes = synthetic_session.read_bytes()

        with pytest.raises(ValueError, match="threshold"):
            trim_session_in_place(synthetic_session, threshold=bad)

        assert synthetic_session.read_bytes() == original_bytes
        assert dir_names(tmp_path) == [synthetic_session.name]


class TestCli:
    """The `aichat trim-in-place` click command (invoked directly)."""

    def test_dry_run_json(
        self, synthetic_session: Path
    ) -> None:
        """--dry-run --json emits ONE pure JSON line; file untouched."""
        original_bytes = synthetic_session.read_bytes()
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [str(synthetic_session), "--dry-run", "--json"],
        )

        assert result.exit_code == 0, result.output
        payload = single_json_line(result.output)
        assert set(payload.keys()) == RESULT_KEYS
        assert payload["dry_run"] is True
        assert payload["applied"] is False
        assert payload["tokens_saved"] > 0
        assert payload["session_file"] == str(synthetic_session)
        assert synthetic_session.read_bytes() == original_bytes

    def test_apply_json_creates_backup(
        self, synthetic_session: Path
    ) -> None:
        """A real CLI apply emits ONE pure JSON line with a live backup."""
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd, [str(synthetic_session), "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = single_json_line(result.output)
        assert payload["applied"] is True
        assert Path(payload["backup_file"]).exists()

    def test_tools_option_filters(
        self, synthetic_session: Path
    ) -> None:
        """--tools limits trimming to the named tool (dry run)."""
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [str(synthetic_session), "-t", "Read", "--dry-run", "--json"],
        )

        assert result.exit_code == 0, result.output
        payload = single_json_line(result.output)
        assert payload["num_tools_trimmed"] == 1

    def test_nonexistent_session_id_errors(
        self, tmp_path: Path
    ) -> None:
        """Unknown session id: exit 1 and ONE pure {"error": ...} line.

        The single-line assertion also guards against banners or
        warnings sneaking in around the JSON on the error path.
        """
        empty_home = tmp_path / "empty-claude-home"
        empty_home.mkdir()
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [
                "zz-no-such-session-93af1c7e55",
                "--json",
                "--claude-home",
                str(empty_home),
            ],
        )

        assert result.exit_code == 1
        payload = single_json_line(result.output)
        assert set(payload.keys()) == {"error"}
        assert "zz-no-such-session-93af1c7e55" in payload["error"]

    def test_ambiguous_session_id_with_json_is_single_json_error(
        self, tmp_path: Path
    ) -> None:
        """Two sessions matching a partial id: --json still emits ONE
        pure {"error": ...} line on stdout with exit 1.

        resolve_session_path() exits via SystemExit (not Exception)
        for ambiguous partial ids; the command must convert that to
        the JSON contract instead of utility text on stdout.
        """
        home = tmp_path / "claude-home"
        project = home / "projects" / "-Users-x-proj"
        project.mkdir(parents=True)
        partial = "zz-ambig-7c4e91d0aa"
        for suffix in ("one", "two"):
            make_synthetic_session(
                project / f"{partial}-{suffix}.jsonl"
            )
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [partial, "--json", "--claude-home", str(home)],
        )

        assert result.exit_code == 1
        # stdout only: resolve_session_path lists matches on stderr,
        # which scripts consuming the JSON contract never parse.
        payload = single_json_line(result.stdout)
        assert set(payload.keys()) == {"error"}
        assert partial in payload["error"]
        # Neither session file was touched.
        for suffix in ("one", "two"):
            name = f"{partial}-{suffix}.jsonl"
            text = (project / name).read_text()
            assert "...truncated" not in text
        assert list(project.glob("*.bak")) == []

    @pytest.mark.parametrize("bad_len", ["0", "-1", "nope"])
    def test_bad_len_with_json_is_single_json_error(
        self, synthetic_session: Path, bad_len: str
    ) -> None:
        """--json + bad --len: exactly one {"error": ...} line, exit 1.

        Click option validation fails before the command body runs,
        but the --json contract (one JSON line, exit 1) must hold even
        then - scripts (the >trim hook) depend on it.
        """
        original_bytes = synthetic_session.read_bytes()
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [str(synthetic_session), "--len", bad_len, "--json"],
        )

        assert result.exit_code == 1
        payload = single_json_line(result.output)
        assert set(payload.keys()) == {"error"}
        assert "--len" in payload["error"]
        assert synthetic_session.read_bytes() == original_bytes

    def test_bad_trim_assistant_with_json_is_single_json_error(
        self, synthetic_session: Path
    ) -> None:
        """--json + non-integer --trim-assistant: one JSON line, exit 1."""
        original_bytes = synthetic_session.read_bytes()
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [str(synthetic_session), "--trim-assistant", "nope", "--json"],
        )

        assert result.exit_code == 1
        payload = single_json_line(result.output)
        assert set(payload.keys()) == {"error"}
        assert "--trim-assistant" in payload["error"]
        assert synthetic_session.read_bytes() == original_bytes

    def test_missing_session_with_json_is_single_json_error(
        self,
    ) -> None:
        """--json with no SESSION argument still honors the contract."""
        runner = CliRunner()

        result = runner.invoke(trim_in_place_cmd, ["--json"])

        assert result.exit_code == 1
        payload = single_json_line(result.output)
        assert set(payload.keys()) == {"error"}
        assert "SESSION" in payload["error"]

    @pytest.mark.parametrize("bad_len", ["0", "-1"])
    def test_bad_len_without_json_is_a_usage_error(
        self, synthetic_session: Path, bad_len: str
    ) -> None:
        """Without --json, Click usage errors keep exit code 2."""
        original_bytes = synthetic_session.read_bytes()
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [str(synthetic_session), "--len", bad_len],
        )

        assert result.exit_code == 2  # click usage error
        assert synthetic_session.read_bytes() == original_bytes

    def test_unexpected_exception_becomes_json_error(
        self,
        synthetic_session: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unexpected exception type still yields {"error": ...}."""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise AttributeError("unexpected boom")

        monkeypatch.setattr(
            trim_in_place_module, "trim_session_in_place", boom
        )
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd, [str(synthetic_session), "--json"]
        )

        assert result.exit_code == 1
        payload = single_json_line(result.output)
        assert set(payload.keys()) == {"error"}
        assert "unexpected boom" in payload["error"]
