"""Registry rename keeps the display label in sync with the handle.

(The bulk of registry tests live in test_agent_tunnel.py; this focused file
keeps that one under the 1000-line limit.)
"""

from __future__ import annotations

from pathlib import Path

from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry

SID = "11111111-2222-3333-4444-555555555555"


def test_rename_syncs_label_to_new_handle(tmp_path: Path) -> None:
    # A `>share <label>` share stores label == handle, so renaming must move
    # the label too — else published/!list show "new (old)" and new threads
    # prefer the stale label via `rec.label or rec.handle`.
    reg = Registry(tmp_path / "registry.json")
    reg.upsert(
        PublishRecord(handle="pay", session_id=SID, cwd="/p", label="pay")
    )
    ok, _ = reg.rename("pay", "billing")
    assert ok
    rec = reg.get("billing")
    assert rec is not None
    assert rec.handle == "billing" and rec.label == "billing"


def test_rename_leaves_custom_label_untouched(tmp_path: Path) -> None:
    # A label that differs from the handle is a deliberate display name; the
    # rename only touches the handle, not that label.
    reg = Registry(tmp_path / "registry.json")
    reg.upsert(
        PublishRecord(
            handle="pay-7q2", session_id=SID, cwd="/p", label="Payments"
        )
    )
    ok, _ = reg.rename("pay-7q2", "billing")
    assert ok
    rec = reg.get("billing")
    assert rec is not None and rec.label == "Payments"
