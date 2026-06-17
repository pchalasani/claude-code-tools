"""ensure_folder_trusted serializes concurrent trust writes to one config.

Forks run on worker threads and may trust different folders in the same shared
config at once; the read-modify-write must not lose entries (Codex P3).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from claude_code_tools.agent_tunnel.trust import (
    TRUST_KEYS,
    ensure_folder_trusted,
)


def test_concurrent_trust_writes_keep_all_entries(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    n = 25
    barrier = threading.Barrier(n)

    def trust(i: int) -> None:
        barrier.wait()  # release together to maximize contention
        ensure_folder_trusted(Path(f"/proj/p{i}"), config)

    threads = [threading.Thread(target=trust, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    projects = json.loads(config.read_text(encoding="utf-8"))["projects"]
    assert len(projects) == n  # no lost updates under contention
    assert all(projects[f"/proj/p{i}"] == TRUST_KEYS for i in range(n))
