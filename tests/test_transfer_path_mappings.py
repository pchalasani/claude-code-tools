"""Equivalent lexical paths cannot select conflicting transfer destinations."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.transfer_paths import normalize_path_mappings


@pytest.mark.parametrize(
    "alias", ["/secondary/", "/secondary/.", "/x/../secondary", "//secondary"]
)
def test_semantic_duplicate_mapping_conflicts(alias: str) -> None:
    """Normalize before checking duplicate source keys."""
    with pytest.raises(ValueError, match="Conflicting destinations"):
        normalize_path_mappings([("/secondary", "/dest-a"), (alias, "/dest-b")])
    assert normalize_path_mappings(
        [("/secondary", "/dest-a"), (alias, "/dest-a/.")]
    ) == {"/secondary": "/dest-a"}


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("conflict", [False, True])
def test_adapters_normalize_mapping_aliases(
    tmp_path: Path, agent: str, conflict: bool
) -> None:
    """Direct and remote adapter entry points share the same mapping semantics."""
    from claude_code_tools import transfer_claude, transfer_codex
    from tests.test_transfer_claude import SID, fixture_home
    from tests.test_transfer_codex import profile, thread

    if agent == "claude":
        home, _ = fixture_home(tmp_path)
        sid = SID
        adapter = transfer_claude
    else:
        home = tmp_path / "codex"
        profile(home)
        thread(home, "root")
        sid = "root"
        adapter = transfer_codex
    mappings = {
        "/secondary": "/dest-a",
        "/secondary/": "/dest-b" if conflict else "/dest-a/.",
    }
    args = (home, sid, tmp_path / "target", Path("/new/project"), tmp_path / "stage")
    if conflict:
        with pytest.raises(ValueError, match="Conflicting destinations"):
            adapter.export_session(*args, path_mappings=mappings)
    else:
        manifest = adapter.export_session(*args, path_mappings=mappings)
        actual = manifest["path_mappings"]
        if isinstance(actual, list):
            actual = {row["source"]: row["destination"] for row in actual}
        assert actual["/secondary"] == "/dest-a"
        assert "/secondary/" not in actual


def test_cli_refuses_semantic_duplicate_before_access(tmp_path: Path) -> None:
    """Conflicting aliases are a usage error before account or SSH inspection."""
    result = CliRunner().invoke(
        main,
        [
            "transfer",
            "unused",
            "--agent",
            "codex",
            "--to",
            "unused-host",
            "--destination-home",
            str(tmp_path / "target"),
            "--destination-project",
            str(tmp_path / "repo"),
            "--map",
            "/secondary",
            "/dest-a",
            "--map",
            "/secondary/",
            "/dest-b",
        ],
    )
    assert result.exit_code == 2
    assert "Conflicting destinations" in result.output
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("suffix", ["", "/", "/.", "/attachments"])
@pytest.mark.parametrize("conflict", [False, True])
def test_codex_home_mapping_cannot_override_profile_destination(
    tmp_path: Path, suffix: str, conflict: bool
) -> None:
    """Operational profile paths agree with where transfer installs profile files."""
    import json

    from claude_code_tools.transfer_codex import export_session
    from tests.test_transfer_codex import profile, thread

    home, destination = tmp_path / "source", tmp_path / "destination"
    profile(home)
    thread(home, "root", mode="legacy")
    rollout = home / "sessions/2026/root.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "cwd": "/old/project",
                    "sandbox_policy": {
                        "type": "workspace-write",
                        "writable_roots": [str(home / "attachments")],
                    },
                },
            }
        )
        + "\n"
    )
    expected = str(destination) + suffix
    mappings = [(str(home) + suffix, "/wrong/profile" if conflict else expected)]
    args = (home, "root", destination, Path("/new/project"), tmp_path / "stage")
    if conflict:
        with pytest.raises(ValueError, match="Account home mapping conflicts"):
            export_session(*args, path_mappings=mappings)
        assert not (tmp_path / "stage").exists()
    else:
        manifest = export_session(*args, path_mappings=mappings)
        copied = json.loads(
            (tmp_path / "stage/files/sessions/2026/root.jsonl").read_text()
        )
        assert copied["payload"]["sandbox_policy"]["writable_roots"] == [
            str(destination / "attachments")
        ]
        assert [
            item for item in manifest["path_mappings"] if item["source"] == str(home)
        ] == [{"source": str(home), "destination": str(destination)}]


@pytest.mark.parametrize("suffix", ["", "/attachments", "/not-created-yet"])
@pytest.mark.parametrize("conflict", [False, True])
def test_codex_account_alias_mapping_guard(
    tmp_path: Path, suffix: str, conflict: bool
) -> None:
    """An account symlink cannot reroute operational metadata outside its profile."""
    import json

    from claude_code_tools.transfer_codex import export_session
    from tests.test_transfer_codex import profile, thread

    home, destination = tmp_path / "real-source", tmp_path / "destination"
    profile(home)
    thread(home, "root", mode="legacy")
    (home / "attachments").mkdir()
    alias = tmp_path / "account-alias"
    alias.symlink_to(home, target_is_directory=True)
    referenced = str(alias) + suffix
    rollout = home / "sessions/2026/root.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "cwd": "/old/project",
                    "sandbox_policy": {
                        "type": "workspace-write",
                        "writable_roots": [referenced],
                    },
                },
            }
        )
        + "\n"
    )
    expected = str(destination) + suffix
    mappings = [(referenced, "/wrong/profile" if conflict else expected)]
    args = (alias, "root", destination, Path("/new/project"), tmp_path / "stage")
    if conflict:
        with pytest.raises(ValueError, match="Account home mapping conflicts"):
            export_session(*args, path_mappings=mappings)
        assert not (tmp_path / "stage").exists()
    else:
        manifest = export_session(*args, path_mappings=mappings)
        copied = json.loads(
            (tmp_path / "stage/files/sessions/2026/root.jsonl").read_text()
        )
        assert copied["payload"]["sandbox_policy"]["writable_roots"] == [expected]
        assert {"source": referenced, "destination": expected} in manifest[
            "path_mappings"
        ]
