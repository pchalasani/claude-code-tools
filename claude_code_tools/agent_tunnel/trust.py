"""Pre-trust a project folder so launching interactive Claude there does not
pop the "Do you trust the files in this folder?" dialog.

There is no CLI flag that skips ONLY the trust dialog while keeping tool
restrictions (`--dangerously-skip-permissions` also drops the read-only
restrictions we rely on), so the supported route is to set the trust flags in
Claude's config (`~/.claude.json`) for the folder. We only ADD/SET keys for
the one project path, never remove anything, and write atomically.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

TRUST_KEYS = {
    "hasTrustDialogAccepted": True,
    "hasTrustDialogHooksAccepted": True,
    "hasCompletedProjectOnboarding": True,
}

# Serializes the read-modify-write below: forks run on worker threads and may
# trust different folders in the same shared config file concurrently. Only
# the daemon writes trust, so an in-process lock (not a cross-process one) is
# what's needed.
_TRUST_LOCK = threading.Lock()


def trust_config_path_for(config_dir: str) -> Path:
    """Trust config file for a Claude config dir (``<dir>/.claude.json``)."""
    return Path(config_dir).expanduser() / ".claude.json"


def default_trust_config_path() -> Path:
    """Path of the config file holding folder-trust state.

    Honors `CLAUDE_CONFIG_DIR` (preferring `<dir>/.claude.json` when it
    exists), else the default `~/.claude.json`.
    """
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg_dir:
        candidate = Path(cfg_dir).expanduser() / ".claude.json"
        if candidate.exists():
            return candidate
    return Path.home() / ".claude.json"


def ensure_folder_trusted(project_dir: Path, config_path: Path) -> bool:
    """Mark `project_dir` trusted in Claude's config; return True if changed.

    Non-destructive: loads the JSON, sets only the trust keys for this one
    project path (merging into any existing entry), and writes atomically. A
    missing/unreadable/non-dict config is left untouched (returns False)
    rather than risk clobbering the user's primary config. The whole
    read-modify-write runs under a process-wide lock and writes via a
    per-process temp file, so concurrent forks trusting different folders
    can't lose each other's entries or race ``os.replace``.
    """
    key = str(project_dir)
    with _TRUST_LOCK:
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
            else:
                data = {}
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            return False

        entry = projects.get(key)
        if not isinstance(entry, dict):
            entry = {}
        if all(entry.get(k) == v for k, v in TRUST_KEYS.items()):
            return False  # already trusted

        entry.update(TRUST_KEYS)
        projects[key] = entry
        tmp = config_path.with_name(
            f"{config_path.name}.agent-tunnel.{os.getpid()}.tmp"
        )
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, config_path)
        return True
