"""Process-boundary tests for the msg maintenance sentinel."""

from __future__ import annotations

import os
import json
import stat
import subprocess
import sys

import pytest

from claude_code_tools.msg import maintenance
from tests.test_msg_migrations import create_frozen_v3_fixture


@pytest.mark.parametrize(
    "stage",
    (
        "after_temp_fsync",
        "after_publish",
        "after_publish_fsync",
        "after_temp_cleanup",
    ),
)
def test_enter_crash_leaves_absent_or_complete_authenticatable_sentinel(
    tmp_path, stage,
):
    db_path = tmp_path / "msg.db"
    script = r"""
import os
import sys
from claude_code_tools.msg import maintenance

def failpoint(actual):
    if actual == sys.argv[2]:
        os._exit(71)

maintenance.enter(sys.argv[1], b"crash-token", _failpoint=failpoint)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path), stage],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 71

    path = maintenance.sentinel_path(db_path)
    if os.path.lexists(path):
        assert stat.S_IMODE(path.lstat().st_mode) == 0o600
        assert maintenance._authorize(db_path, b"crash-token")["generation"]
    else:
        maintenance.enter(db_path, b"crash-token")
        assert maintenance._authorize(db_path, b"crash-token")["generation"]


def test_dangling_sentinel_symlink_still_blocks_initialization(tmp_path):
    db_path = tmp_path / "msg.db"
    path = maintenance.sentinel_path(db_path)
    path.symlink_to(tmp_path / "missing")

    assert maintenance.is_active(db_path)


@pytest.mark.parametrize(
    "stage",
    ("before_schema_migration", "after_schema_migration", "after_sentinel_update"),
)
def test_migrate_crash_is_retryable(tmp_path, stage):
    db_path = tmp_path / "legacy-v3.db"
    create_frozen_v3_fixture(db_path)
    maintenance.enter(db_path, b"crash-token")
    script = r"""
import os
import sys
from claude_code_tools.msg import maintenance

def failpoint(actual):
    if actual == sys.argv[2]:
        os._exit(72)

maintenance.migrate(sys.argv[1], b"crash-token", _failpoint=failpoint)
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path), stage],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 72
    retried = maintenance.migrate(db_path, b"crash-token")
    assert retried["to_schema_version"] == 4


@pytest.mark.parametrize("stage", ("before_unlink", "after_unlink", "after_dir_fsync"))
def test_exit_crash_is_present_retryable_or_absent_complete(tmp_path, stage):
    db_path = tmp_path / "msg.db"
    from claude_code_tools.msg.store import MsgStore

    MsgStore(db_path)
    entered = maintenance.enter(db_path, b"crash-token")
    maintenance.migrate(db_path, b"crash-token")
    postcheck = {
        "schema": "msg.maintenance.postcheck.v1",
        "generation": entered["generation"],
        "db_wal_shm_unchanged_after_negative_mutation": True,
        "row_counts_unchanged_after_negative_mutation": True,
    }
    script = r"""
import json
import os
import sys
from claude_code_tools.msg import maintenance

def failpoint(actual):
    if actual == sys.argv[2]:
        os._exit(73)

maintenance.exit_mode(
    sys.argv[1], b"crash-token", json.loads(sys.stdin.read()),
    _failpoint=failpoint,
)
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path), stage],
        input=json.dumps(postcheck),
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 73
    if maintenance.is_active(db_path):
        assert maintenance.exit_mode(db_path, b"crash-token", postcheck)
    else:
        assert maintenance.status(db_path) == {"active": False}
