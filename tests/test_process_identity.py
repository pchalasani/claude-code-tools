"""Process-start identity tests for long-lived TUI registrations."""

from __future__ import annotations

import subprocess

from claude_code_tools.process_identity import process_start_identity


def test_linux_identity_parses_stat_with_spaces_and_parentheses(tmp_path):
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "4242"
    process_dir.mkdir(parents=True)
    fields_after_comm = ["S"] + [str(index) for index in range(4, 23)]
    # Field 22 (starttime) is the final value in this minimal fixture.
    fields_after_comm[-1] = "987654"
    (process_dir / "stat").write_text(
        "4242 (codex worker (main)) " + " ".join(fields_after_comm),
        encoding="utf-8",
    )

    assert process_start_identity(
        4242, platform="linux", proc_root=proc_root,
    ) == "linux:4242:987654"


def test_linux_identity_fails_closed_for_missing_or_malformed_stat(tmp_path):
    proc_root = tmp_path / "proc"
    (proc_root / "1").mkdir(parents=True)
    (proc_root / "1" / "stat").write_text("broken", encoding="utf-8")

    assert process_start_identity(1, platform="linux", proc_root=proc_root) is None
    assert process_start_identity(2, platform="linux", proc_root=proc_root) is None


def test_macos_identity_uses_bounded_ps_output(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, stdout="Mon Jan  1 00:00:00 2026\n", stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)

    assert process_start_identity(99, platform="darwin") == (
        "macos:99:Mon Jan 1 00:00:00 2026"
    )
    assert calls == [
        (
            ["ps", "-o", "lstart=", "-p", "99"],
            {"capture_output": True, "text": True, "timeout": 5},
        )
    ]


def test_unknown_platform_fails_closed():
    assert process_start_identity(7, platform="windows") is None
