"""Append immutable dated updates to a visual brief."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from visual_brief.writes.legacy import read_for_write, settle_legacy_pairs
from visual_brief.writes.lint import report_lint
from visual_brief.writes.runfiles import CliError, resolve_run, save_document


def add_update_command(runs_root: Path, run_id: str | None, update: Any) -> int:
    """Append one dated update without changing any update already saved.

    An update whose id is ``now`` is ordinary history. Keeping that existing
    id is the migration from the former pinned panel: conversation anchors
    keep the same path, while future publishes append updates with new ids.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        update: The update, as a JSON object.

    Returns:
        The process exit status.

    Raises:
        CliError: If the update is not a dated, uniquely identified object,
            or the resulting document would not validate.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    if not isinstance(update, dict):
        raise CliError("an update must be a JSON object")
    update_id = update.get("id")
    if not isinstance(update_id, str) or not update_id.strip():
        raise CliError("the update must carry a non-empty id")
    stamp = update.get("timestamp")
    if not isinstance(stamp, str) or not stamp.strip():
        raise CliError("a dated update must carry a timestamp")

    document, legacy = read_for_write(run_dir)
    updates = _updates(document)
    if any(
        isinstance(existing, dict) and existing.get("id") == update_id
        for existing in updates
    ):
        raise CliError(
            f"update {update_id!r} already exists; updates are appended, "
            "never rewritten"
        )
    updates.append(update)
    settle_legacy_pairs(run_dir, document, legacy)
    index_path = save_document(run_dir, document)
    print(f"add-update: appended {update_id}; rendered {index_path}")
    report_lint(run_dir, document)
    return 0


def _updates(document: Any) -> list[Any]:
    """Return the document's own updates list."""
    updates = document.get("updates") if isinstance(document, dict) else None
    if not isinstance(updates, list):
        raise CliError("content.json has no updates list")
    return updates
