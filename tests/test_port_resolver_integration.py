"""Regression tests for `aichat port` lookups via the shared resolver.

Every previously working port lookup form must keep working now
that ``resolve_port_session`` delegates to
:mod:`claude_code_tools.resolve_session`: full ids, id prefixes,
mid-id and suffix fragments, codex timestamp and ``rollout-``
filename fragments, direct paths, and ambiguity rejection — plus
the new name-based lookup. Kept separate from
``test_port_session.py`` for the repo's file-length limit.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.port_service import (
    PortSessionError,
    ResolvedSession,
    resolve_port_session,
)
from claude_code_tools.session_utils import encode_claude_project_path
from tests.test_port_session import (
    LEGACY_UUID,
    MODERN_UUID,
    _msg,
    _resp,
    _session_meta,
    _ts,
    write_legacy_rollout,
    write_modern_rollout,
    write_rollout_lines,
)
from tests.resolve_session_helpers import (
    _create_threads_database,
    _insert_codex_thread,
)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create the fake project directory ported sessions refer to."""
    d = tmp_path / "myproj"
    d.mkdir()
    return d


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    """Create an isolated Claude home directory."""
    d = tmp_path / "claude-home"
    d.mkdir()
    return d


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    """Create an isolated Codex home directory."""
    d = tmp_path / "codex-home"
    d.mkdir()
    return d


@pytest.fixture
def runner() -> CliRunner:
    """Return an isolated in-process Click runner."""
    return CliRunner()


def _write_claude_session(
    claude_home: Path,
    session_id: str,
    project_dir: Path,
    title: str | None = None,
) -> Path:
    """Write a minimal portable Claude session, optionally named."""
    proj = claude_home / "projects" / encode_claude_project_path(
        str(project_dir)
    )
    proj.mkdir(parents=True, exist_ok=True)
    lines: list[dict[str, object]] = [
        {
            "parentUuid": None,
            "isSidechain": False,
            "cwd": str(project_dir),
            "sessionId": session_id,
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "uuid": str(uuid.uuid4()),
            "timestamp": _ts(0),
        }
    ]
    if title is not None:
        lines.append(
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": title,
            }
        )
    path = proj / f"{session_id}.jsonl"
    path.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    return path


def _resolve(
    session: str, claude_home: Path, codex_home: Path
) -> ResolvedSession:
    """Resolve a port query against the two isolated fake homes."""
    return resolve_port_session(
        session,
        claude_home=str(claude_home),
        codex_home=str(codex_home),
    )


class TestLookupForms:
    """Every supported non-path query form resolves to one session."""

    def test_full_codex_id(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        rollout = write_modern_rollout(codex_home, project_dir)
        resolved = _resolve(MODERN_UUID, claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_full_claude_id(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        sid = str(uuid.uuid4())
        path = _write_claude_session(claude_home, sid, project_dir)
        resolved = _resolve(sid, claude_home, codex_home)
        assert resolved.agent == "claude"
        assert resolved.session_file == path.resolve()

    def test_uuid_prefix(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        rollout = write_modern_rollout(codex_home, project_dir)
        resolved = _resolve(MODERN_UUID[:13], claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_uuid_middle_fragment(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        # MODERN_UUID = "019f6d85-df3c-7c83-84f6-b97e73305bbb"
        rollout = write_modern_rollout(codex_home, project_dir)
        resolved = _resolve("7c83-84f6", claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_uuid_suffix_fragment(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        rollout = write_modern_rollout(codex_home, project_dir)
        resolved = _resolve("b97e73305bbb", claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_codex_timestamp_fragment(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        # write_modern_rollout files are named
        # rollout-2026-07-16T20-41-57-<uuid>.jsonl
        rollout = write_modern_rollout(codex_home, project_dir)
        resolved = _resolve("2026-07-16T20-41", claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_rollout_prefixed_fragment(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        rollout = write_modern_rollout(codex_home, project_dir)
        resolved = _resolve(
            "rollout-2026-07-16T20-41-57", claude_home, codex_home
        )
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_claude_session_name(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        sid = str(uuid.uuid4())
        path = _write_claude_session(
            claude_home, sid, project_dir, title="ported-by-name"
        )
        resolved = _resolve("ported-by-name", claude_home, codex_home)
        assert resolved.agent == "claude"
        assert resolved.session_file == path.resolve()

    def test_codex_session_name(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        rollout = write_modern_rollout(codex_home, project_dir)
        database = codex_home / "state_1.sqlite"
        _create_threads_database(database)
        _insert_codex_thread(
            database,
            MODERN_UUID,
            rollout,
            str(project_dir),
            "codex-thread-name",
            1_720_000_000,
        )
        resolved = _resolve("codex-thread-name", claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_codex_rollout_missing_from_state_db(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        """Rollouts the sqlite state DB has not indexed still port."""
        indexed = write_modern_rollout(codex_home, project_dir)
        database = codex_home / "state_1.sqlite"
        _create_threads_database(database)
        _insert_codex_thread(
            database,
            MODERN_UUID,
            indexed,
            str(project_dir),
            "Indexed Thread",
            1_720_000_000,
        )
        omitted_id = "019f6d85-aaaa-7aaa-8aaa-b97e73305aaa"
        lines = [
            _session_meta(0, omitted_id, str(project_dir)),
            _resp(1, _msg("user", "Unindexed question")),
            _resp(2, _msg("assistant", "Answer", "output_text")),
        ]
        omitted = write_rollout_lines(codex_home, omitted_id, lines)
        resolved = _resolve(omitted_id, claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == omitted.resolve()

    def test_corrupt_codex_state_db_falls_back_to_disk(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        """A broken state database degrades to direct rollout lookup.

        Port lookups must keep finding valid on-disk rollouts even
        when the highest-numbered state database is corrupt, instead
        of masking the database error as "session not found".
        """
        rollout = write_modern_rollout(codex_home, project_dir)
        (codex_home / "state_9.sqlite").write_bytes(
            b"not a sqlite database"
        )
        resolved = _resolve(MODERN_UUID, claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_incomplete_codex_state_db_falls_back_to_disk(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        """A threads table missing required columns degrades to disk."""
        rollout = write_modern_rollout(codex_home, project_dir)
        connection = sqlite3.connect(codex_home / "state_9.sqlite")
        try:
            connection.execute("CREATE TABLE threads (id TEXT)")
            connection.commit()
        finally:
            connection.close()
        resolved = _resolve(MODERN_UUID, claude_home, codex_home)
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()

    def test_legacy_rollout_ports_by_full_id(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        write_legacy_rollout(codex_home, project_dir)
        result = runner.invoke(
            main,
            [
                "port",
                LEGACY_UUID,
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )


class TestRejections:
    """Queries that must not resolve, and ambiguity rendering."""

    @pytest.mark.parametrize("query", ["", "   ", "a/b", "a\\b", "*", "??"])
    def test_non_matching_queries_report_not_found(
        self,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
        query: str,
    ) -> None:
        write_modern_rollout(codex_home, project_dir)
        with pytest.raises(PortSessionError, match="not found"):
            _resolve(query, claude_home, codex_home)

    @pytest.mark.parametrize(
        "query", ["a/b", "a\\b", "*", "??", "has*star", "what?"]
    )
    def test_separator_and_glob_queries_reject_matching_names(
        self,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
        query: str,
    ) -> None:
        """Rejected queries stay unmatched even for equal titles.

        Both a claude session and a codex thread are literally NAMED
        the query string; the port lookup must still report not
        found, because path separators and glob metacharacters are
        rejected before any resolution tier runs.
        """
        sid = str(uuid.uuid4())
        _write_claude_session(claude_home, sid, project_dir, title=query)
        rollout = write_modern_rollout(codex_home, project_dir)
        database = codex_home / "state_1.sqlite"
        _create_threads_database(database)
        _insert_codex_thread(
            database,
            MODERN_UUID,
            rollout,
            str(project_dir),
            query,
            1_720_000_000,
        )
        with pytest.raises(PortSessionError, match="not found"):
            _resolve(query, claude_home, codex_home)

    def test_cross_agent_singles_are_ambiguous(
        self, claude_home: Path, codex_home: Path, project_dir: Path
    ) -> None:
        """One claude and one codex match must be rejected together."""
        sid = str(uuid.uuid4())
        _write_claude_session(
            claude_home, sid, project_dir, title="xshared-name"
        )
        rollout = write_modern_rollout(codex_home, project_dir)
        database = codex_home / "state_1.sqlite"
        _create_threads_database(database)
        _insert_codex_thread(
            database,
            MODERN_UUID,
            rollout,
            str(project_dir),
            "xshared-name",
            1_720_000_000,
        )
        with pytest.raises(PortSessionError) as exc_info:
            _resolve("xshared-name", claude_home, codex_home)
        message = str(exc_info.value)
        assert "Ambiguous session 'xshared-name' matches 2" in message
        assert "[claude]" in message
        assert "[codex]" in message
        assert sid in message
        assert MODERN_UUID in message
        assert "(xshared-name)" in message
        assert "modified" in message

    def test_ambiguity_listing_is_capped_with_tail(
        self, claude_home: Path, codex_home: Path, tmp_path: Path
    ) -> None:
        for index in range(30):
            sid = f"{index:08x}-0000-4000-8000-{index:012x}"
            _write_claude_session(
                claude_home,
                sid,
                tmp_path / f"proj-{index}",
                title="Crowded Port Name",
            )
        with pytest.raises(PortSessionError) as exc_info:
            _resolve("Crowded Port Name", claude_home, codex_home)
        message = str(exc_info.value)
        assert "matches 30 sessions" in message
        assert message.count("[claude]") == 25
        assert "... and 5 more" in message

    def test_name_ported_via_cli(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        """`aichat port <name>` converts the named claude session."""
        sid = str(uuid.uuid4())
        _write_claude_session(
            claude_home, sid, project_dir, title="cli-name-port"
        )
        result = runner.invoke(
            main,
            [
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
                "port",
                "cli-name-port",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: claude — porting to Codex"
            in result.output
        )
        assert "New Codex session id:" in result.output
        assert list((codex_home / "sessions").rglob("rollout-*.jsonl"))


# ---------------------------------------------------------------------
# E2E EVIDENCE (real-home resolver-integration verification, run
# 2026-07-17 against this working tree via the editable .venv;
# output trimmed; all created artifacts were deleted afterwards,
# with the read-only checks below proving removal).
#
# (a) NAME-BASED PORT (claude -> codex) + codex resume.
#
#   Finding the custom-titled session:
#   $ grep -rlh '"type":"custom-title"\|"type": "custom-title"' \
#       ~/.claude/projects --include='*.jsonl' | head -5
#   ...
#   /Users/pchalasani/.claude/projects/\
#     -Users-pchalasani-Git-coa-policy-translation/\
#     ec003409-131c-4e9c-820c-1809ff320e49.jsonl
#   ...
#   $ grep -h '"type":"custom-title"' /Users/pchalasani/.claude/\
#       projects/-Users-pchalasani-Git-coa-policy-translation/\
#       ec003409-131c-4e9c-820c-1809ff320e49.jsonl | tail -1
#   {"type":"custom-title","customTitle":"translation-test",
#    "sessionId":"ec003409-131c-4e9c-820c-1809ff320e49"}
#
#   $ .venv/bin/aichat port translation-test
#   Detected source agent: claude — porting to Codex
#   New Codex session id:  019f70a6-8904-7722-9bb3-348fca49d41c
#   Output file: /Users/pchalasani/.codex/sessions/2026/07/17/\
#     rollout-2026-07-17T11-16-30-019f70a6-8904-7722-9bb3-348fca49d41c.jsonl
#   Session cwd: /Users/pchalasani/Git/coa-policy-translation
#   To resume:
#     cd /Users/pchalasani/Git/coa-policy-translation && \
#       codex resume 019f70a6-8904-7722-9bb3-348fca49d41c
#   EXIT=0
#
#   $ cd /Users/pchalasani/Git/coa-policy-translation && timeout 240 \
#     /Users/pchalasani/.nvm/versions/node/v22.23.0/bin/codex exec resume \
#       019f70a6-8904-7722-9bb3-348fca49d41c --skip-git-repo-check \
#       -c 'sandbox_mode="read-only"' "Reply with exactly OK" < /dev/null
#   codex
#   OK
#   EXIT_CODE=0
#
#   Cleanup (exact commands, then removal verified read-only):
#   $ rm /Users/pchalasani/.codex/sessions/2026/07/17/\
#       rollout-2026-07-17T11-16-30-019f70a6-8904-7722-9bb3-348fca49d41c.jsonl
#   $ grep -v '"session_id": "019f70a6-8904-7722-9bb3-348fca49d41c"' \
#       /Users/pchalasani/.codex/history.jsonl \
#       > /Users/pchalasani/.codex/history.jsonl.tmp \
#     && mv /Users/pchalasani/.codex/history.jsonl.tmp \
#       /Users/pchalasani/.codex/history.jsonl
#   $ ls /Users/pchalasani/.codex/sessions/2026/07/17/\
#       rollout-2026-07-17T11-16-30-019f70a6-8904-7722-9bb3-348fca49d41c.jsonl
#   ls: ... No such file or directory
#   $ grep -c 019f70a6 /Users/pchalasani/.codex/history.jsonl
#   0
#
# (b) REGRESSION: PARTIAL-CODEX-ID PORT (codex -> claude) + resume.
#
#   $ .venv/bin/aichat port 89c3-7022
#     (mid-id fragment of codex session
#      019ef71e-89c3-7022-a4dd-63315c2044ad, a real rollout at
#      /Users/pchalasani/.codex/sessions/2026/06/23/\
#      rollout-2026-06-23T20-53-54-019ef71e-89c3-7022-a4dd-63315c2044ad.jsonl)
#   Detected source agent: codex — porting to Claude Code
#   New Claude session id: 375cee14-9dfe-4f5f-accb-68b5f40c3c4a
#   Output file: /Users/pchalasani/.claude/projects/\
#     -Users-pchalasani-Git-observability-feat-nd2dl/\
#     375cee14-9dfe-4f5f-accb-68b5f40c3c4a.jsonl
#   Session cwd: /Users/pchalasani/Git/observability.feat-nd2dl
#   EXIT=0
#
#   $ cd /Users/pchalasani/Git/observability.feat-nd2dl && timeout 240 \
#     claude --resume 375cee14-9dfe-4f5f-accb-68b5f40c3c4a \
#       -p "Reply with exactly OK"
#   OK
#   EXIT_CODE=0
#
#   Cleanup (exact command, then removal verified read-only):
#   $ rm /Users/pchalasani/.claude/projects/\
#       -Users-pchalasani-Git-observability-feat-nd2dl/\
#       375cee14-9dfe-4f5f-accb-68b5f40c3c4a.jsonl
#   $ ls /Users/pchalasani/.claude/projects/\
#       -Users-pchalasani-Git-observability-feat-nd2dl/\
#       375cee14-9dfe-4f5f-accb-68b5f40c3c4a.jsonl
#   ls: ... No such file or directory
#   $ grep -l 375cee14 /Users/pchalasani/.claude/projects/\
#       -Users-pchalasani-Git-observability-feat-nd2dl/*.jsonl
#   (no matches — no forked transcript remains either)
# ---------------------------------------------------------------------
