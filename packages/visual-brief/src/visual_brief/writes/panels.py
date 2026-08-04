"""Append compatibility briefing records to a visual brief."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from visual_brief.writes.legacy import read_for_write, settle_legacy_pairs
from visual_brief.writes.lint import report_lint
from visual_brief.writes.runfiles import (
    CliError,
    resolve_run,
    save_document,
    write_transaction,
)


def add_update_command(runs_root: Path, run_id: str | None, update: Any) -> int:
    """Append one briefing through the compatibility/import command.

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
        CliError: If the update is not a timestamped, uniquely identified
            object, or the resulting document would not validate.
    """
    print(
        "warning: add-update is for compatibility imports; "
        "normal briefings use publish",
        file=sys.stderr,
    )
    _, run_dir = resolve_run(runs_root, run_id)
    if not isinstance(update, dict):
        raise CliError("an update must be a JSON object")
    update_id = update.get("id")
    if not isinstance(update_id, str) or not update_id.strip():
        raise CliError("the update must carry a non-empty id")
    stamp = update.get("timestamp")
    if not isinstance(stamp, str) or not stamp.strip():
        raise CliError("an imported briefing must carry a timestamp")

    with write_transaction(run_dir):
        document, legacy = read_for_write(run_dir)
        append_update(document, update)
        settle_legacy_pairs(run_dir, document, legacy)
        index_path = save_document(run_dir, document)
    print(f"add-update: appended {update_id}; rendered {index_path}")
    report_lint(run_dir, document)
    return 0


def append_update(document: Any, update: dict[str, Any]) -> None:
    """Append an independent update after checking its stable identity.

    Args:
        document: Stored visual brief document being changed in memory.
        update: Validated or compatibility update to append.

    Raises:
        CliError: If the document has no update list or the id already exists.
    """
    updates = _updates(document)
    update_id = update.get("id")
    if any(
        isinstance(existing, dict) and existing.get("id") == update_id
        for existing in updates
    ):
        raise CliError(
            f"update {update_id!r} already exists; updates are appended, "
            "never rewritten"
        )
    updates.append(copy.deepcopy(update))


def _updates(document: Any) -> list[Any]:
    """Return the document's own updates list."""
    updates = document.get("updates") if isinstance(document, dict) else None
    if not isinstance(updates, list):
        raise CliError("content.json has no updates list")
    return updates
