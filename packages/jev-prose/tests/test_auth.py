"""Exercise auth subprocess isolation without accessing real credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


WRANGLER = '''#!/usr/bin/env python3
import json
import pathlib
import sys

args = sys.argv[1:]
root = (pathlib.Path(args[args.index("--cwd") + 1])
        if "--cwd" in args else pathlib.Path.cwd())
# Model Wrangler's documented dotenv lookup at its configured working directory.
token = "global-oauth-fixture"
if (root / ".env").exists():
    token = "wrong-project-token"
print(json.dumps({"type": "oauth", "token": token}))
'''


def run_auth(tmp_path: Path, explicit_token: str | None = None) -> dict[str, str]:
    """Run real Python and CLI processes inside a project with unrelated auth."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "wrangler"
    executable.write_text(WRANGLER)
    executable.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("CLOUDFLARE_API_TOKEN=wrong-project-token\n")
    env = dict(os.environ)
    env.pop("CLOUDFLARE_API_TOKEN", None)
    if explicit_token is not None:
        env["CLOUDFLARE_API_TOKEN"] = explicit_token
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c",
         "import json; from jev_prose.backend import cloudflare_token; "
         "print(json.dumps({'token': cloudflare_token()}))"],
        env=env, cwd=project, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_wrangler_fallback_ignores_project_dotenv(tmp_path: Path) -> None:
    """An unrelated repo token must not replace the user's global OAuth login."""
    assert run_auth(tmp_path)["token"] == "global-oauth-fixture"


def test_explicit_environment_token_still_wins(tmp_path: Path) -> None:
    """Caller-supplied credentials retain their documented precedence."""
    assert run_auth(tmp_path, "explicit-fixture")["token"] == "explicit-fixture"
