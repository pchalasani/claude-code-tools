"""Check that the CLI bounds its inputs and never crashes on bad config."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from jev_prose.detector import CONTEXT_LIMIT, TEXT_LIMIT

OVERSIZE = 4_000_000
CHUNK = "lorem ipsum dolor sit amet " * 1000
# A bounded reader cannot accept more than its cap plus the pipe capacity and
# one text buffer, which stay far below this on any ordinary machine.
ACCEPTABLE = 500_000


def cli_environment() -> dict[str, str]:
    """Run the real CLI without inheriting the user's own credentials."""
    env = dict(os.environ)
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "SYSONE_CONFIG",
                 "JEV_PROSE_API_KEY", "TYPESAFE_API_KEY"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def feed(fifo: Path, offered: list[int]) -> None:
    """Offer OVERSIZE characters and record how many the reader accepted.

    Args:
        fifo: Named pipe the CLI reads its input from.
        offered: Single-element output list; writes stop once the reader closes.
    """
    written = 0
    try:
        with fifo.open("w", encoding="utf-8") as stream:
            while written < OVERSIZE:
                stream.write(CHUNK)
                written += len(CHUNK)
    except OSError:
        pass
    offered.append(written)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")
@pytest.mark.parametrize("flag", [None, "--context"])
def test_oversize_input_is_rejected_without_reading_it_all(
    tmp_path: Path, flag: str | None,
) -> None:
    """An oversize file or context fails validation after a bounded read."""
    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo)
    draft = tmp_path / "draft.txt"
    draft.write_text("A short, perfectly ordinary draft.", encoding="utf-8")
    # An explicit endpoint keeps the run credential-free; the size check in
    # check() runs before any request, so nothing is ever sent there.
    command = [sys.executable, "-m", "jev_prose.cli", "check",
               "--url", "http://127.0.0.1:1/v1/systemone"]
    command += [str(draft), flag, str(fifo)] if flag else [str(fifo)]
    offered: list[int] = []
    writer = threading.Thread(target=feed, args=(fifo, offered), daemon=True)
    writer.start()
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=60, check=False,
        env=cli_environment(),
    )
    writer.join(timeout=30)

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["ran"] is False and report["ok"] is False
    assert str(TEXT_LIMIT) in report["error"]
    assert str(CONTEXT_LIMIT) in report["error"]
    assert offered and offered[0] < ACCEPTABLE, (
        f"CLI accepted {offered} characters; a bounded read stops near its cap"
    )


def test_malformed_config_section_is_a_structured_error(tmp_path: Path) -> None:
    """A non-table cloudflare section reports exit 2 rather than a traceback."""
    config = tmp_path / "config.toml"
    config.write_text('cloudflare = "not-a-table"\n', encoding="utf-8")
    draft = tmp_path / "draft.txt"
    draft.write_text("A short, perfectly ordinary draft.", encoding="utf-8")
    env = cli_environment()
    env["SYSONE_CONFIG"] = str(config)
    result = subprocess.run(
        [sys.executable, "-m", "jev_prose.cli", "check", str(draft)],
        capture_output=True, text=True, timeout=60, check=False, env=env,
    )

    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert report["ran"] is False and report["ok"] is False
    assert "cloudflare" in report["error"]
