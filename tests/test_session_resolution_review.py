"""Regression tests for reviewed shared-session resolution edge cases."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claude_code_tools.session_resolution import (
    ResolvedSessionQuery,
    SessionQueryError,
    resolve_session_query,
)
from tests.resolve_session_helpers import (
    FakeHome,
    _insert_codex_thread,
    _write_claude_session,
    _write_codex_session,
    claude_home,
    codex_home,
)

__all__ = ["claude_home", "codex_home"]


def _resolve(
    query: str,
    claude: FakeHome,
    codex: FakeHome,
    *,
    agent: str | None = None,
) -> ResolvedSessionQuery:
    """Resolve a query against isolated test homes."""
    return resolve_session_query(
        query,
        agent=agent,
        claude_home=str(claude.path),
        codex_home=str(codex.path),
    )


def test_long_hex_exact_name_outranks_id_fragment(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """An 8+ character exact name outranks another session's partial ID."""
    query = "deadbeef"
    named = _write_claude_session(
        claude_home.path,
        "eeee4444-4444-4444-8444-444444444444",
        claude_home.directories[0],
        query,
        1_720_000_000.0,
    )
    _write_claude_session(
        claude_home.path,
        f"ffff{query}-beef-4444-8444-444444444444",
        claude_home.directories[1],
        "Different session",
        1_720_000_001.0,
    )

    result = _resolve(
        query, claude_home, codex_home, agent="claude"
    )

    assert result.session_file == named.resolve()


def test_id_lookup_preserves_discovered_symlink(
    tmp_path: Path,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """ID resolution retains an in-home symlink rather than its referent."""
    external = tmp_path / "external-session.jsonl"
    external.write_bytes(claude_home.files[2].read_bytes())
    discovered = claude_home.files[2]
    discovered.unlink()
    discovered.symlink_to(external)

    result = _resolve(
        claude_home.ids[2],
        claude_home,
        codex_home,
        agent="claude",
    )

    assert result.session_file == discovered.absolute()
    assert result.session_file.is_symlink()
    assert result.session_file.resolve() == external.resolve()


def test_constrained_invalid_home_surfaces_resolver_error(
    tmp_path: Path,
) -> None:
    """A missing constrained home is not mislabeled as a missing session."""
    missing = tmp_path / "missing-claude-home"

    with pytest.raises(SessionQueryError, match="Home does not exist"):
        resolve_session_query(
            "deadbeef",
            agent="claude",
            claude_home=str(missing),
        )


def test_failed_other_agent_is_suppressed_when_match_exists(
    tmp_path: Path,
    codex_home: FakeHome,
) -> None:
    """A usable result wins over another agent home's operational failure."""
    missing = tmp_path / "missing-claude-home"

    result = resolve_session_query(
        codex_home.ids[2],
        claude_home=str(missing),
        codex_home=str(codex_home.path),
    )

    assert result.agent == "codex"
    assert result.session_file == codex_home.files[2].resolve()


def test_stray_codex_state_file_cannot_bypass_exact_name(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A nonnumeric state filename does not defeat exact-name precedence."""
    query = "deadbeef"
    database = codex_home.path / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE threads SET title = ? WHERE id = ?",
            (query, codex_home.ids[0]),
        )
        connection.commit()
    fragment = _write_codex_session(
        codex_home.path,
        f"eeee{query}-4444-4444-8444-444444444444",
        codex_home.directories[1],
        1_720_000_000.0,
    )
    (codex_home.path / "state_backup.sqlite").touch()

    result = _resolve(
        query, claude_home, codex_home, agent="codex"
    )

    assert fragment != codex_home.files[0]
    assert result.session_file == codex_home.files[0].resolve()


def test_database_id_prefix_outranks_active_mid_id_fragment(
    tmp_path: Path,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A database-only ID prefix forces full tiered resolution."""
    query = "deadbeef"
    archived_id = "deadbeef-1111-4111-8111-111111111111"
    active_id = "ffff1111-beef-4111-8111-1111deadbeef"
    archived_source = _write_codex_session(
        codex_home.path,
        archived_id,
        str(tmp_path / "archived"),
        1_720_000_000.0,
    )
    archived = codex_home.path / "archived_sessions" / archived_source.name
    archived.parent.mkdir()
    archived_source.rename(archived)
    active = _write_codex_session(
        codex_home.path,
        active_id,
        str(tmp_path / "active"),
        1_720_000_001.0,
    )
    _insert_codex_thread(
        codex_home.path / "state_5.sqlite",
        archived_id,
        archived,
        str(tmp_path / "archived"),
        "Archived exact prefix",
        1_720_000_000,
        archived=True,
    )

    result = _resolve(
        query, claude_home, codex_home, agent="codex"
    )

    assert query in active.name
    assert result.session_file == archived.resolve()
