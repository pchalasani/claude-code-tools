"""Regression coverage for CLI publishing concurrency."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from visual_brief.cli import new_command


def test_render_waits_for_a_concurrent_content_write(tmp_path: Path) -> None:
    """Render reads content only after the run's current writer finishes."""
    runs_root = tmp_path / "runs"
    assert new_command(runs_root, "Safe", "safe-run") == 0
    run_dir = runs_root / "safe-run"
    environment = os.environ.copy()
    environment["VISUAL_BRIEF_HOME"] = str(runs_root)
    lock_path = run_dir / ".write.lock"

    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "visual_brief.cli",
                "render",
                "safe-run",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=1)
        content_path = run_dir / "content.json"
        concurrent = json.loads(content_path.read_text(encoding="utf-8"))
        concurrent["title"] = "Rendered after the concurrent write"
        content_path.write_text(
            json.dumps(concurrent, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    stdout, stderr = process.communicate(timeout=30)

    assert (process.returncode, stderr) == (0, "")
    assert str(run_dir / "index.html") in stdout
    assert "Rendered after the concurrent write" in (
        run_dir / "index.html"
    ).read_text(encoding="utf-8")
