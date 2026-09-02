"""Direct briefing publishing and one-time legacy-state migration."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from page_document import embedded_document
from write_support import make_run, queue_line, read_content_file, write_content
from visual_brief.cli import main, new_command
from visual_brief.render.embed import project_document
from visual_brief.schema import (
    CURRENT_STATE_ROOT,
    current_state_item_path,
    current_state_lane_path,
)
from visual_brief.server.counting import (
    count_unanswered_questions,
    reply_target_error,
)
from visual_brief.writes import (
    CliError,
    answer_command,
    fold_command,
    lint_document,
    publish_command,
)
from visual_brief.writes import runfiles

BRIEFING = {
    "id": "one-briefing-contract",
    "timestamp": "2026-08-04T12:00:00Z",
    "headline": "Publishing now accepts one complete briefing",
    "summary": "The latest report and its durable history are one record.",
    "lanes": [
        {
            "id": "delivery",
            "name": "What works now",
            "items": [
                {
                    "id": "publisher",
                    "glance": "The direct publisher is working.",
                    "explanation": (
                        "It validates and appends one complete briefing."
                    ),
                    "trust": "verified-by-me",
                    "forensics": ["focused publisher check: passed"],
                }
            ],
        },
        {
            "id": "next",
            "name": "What comes next",
            "items": [
                {
                    "id": "review",
                    "glance": "The final review is still pending.",
                    "explanation": "A reviewer must inspect the final tree.",
                    "trust": "reported-by-agent",
                }
            ],
        },
    ],
}

LEGACY_STATE = {
    "updated_at": "2026-08-04T11:00:00Z",
    "headline": "The earlier current state remains readable",
    "summary": "Its conversations will move into an ordinary briefing.",
    "lanes": [
        {
            "id": "legacy-lane",
            "name": "Earlier work",
            "items": [
                {
                    "id": "legacy-item",
                    "glance": "The earlier item remains intact.",
                    "explanation": "It keeps its evidence and conversation.",
                    "trust": "reported-by-agent",
                }
            ],
        }
    ],
}


def briefing(
    update_id: str = "one-briefing-contract",
    timestamp: str = "2026-08-04T12:00:00Z",
) -> dict[str, Any]:
    """Return an independent valid direct briefing."""
    value = copy.deepcopy(BRIEFING)
    value["id"] = update_id
    value["timestamp"] = timestamp
    return value


def run_bytes(run_dir: Path) -> dict[str, bytes]:
    """Snapshot every material run file by name."""
    return {
        name: (run_dir / name).read_bytes()
        for name in (
            "content.json",
            "index.html",
            "meta.json",
            "questions.jsonl",
        )
    }


def update_owner(
    document: dict[str, Any],
    update_id: str,
    owner: str,
) -> dict[str, Any]:
    """Return an update root, lane, or item by id."""
    update = next(
        entry for entry in document["updates"] if entry["id"] == update_id
    )
    if owner == "root":
        return update
    for lane in update["lanes"]:
        if lane["id"] == owner:
            return lane
        for item in lane["items"]:
            if item["id"] == owner:
                return item
    raise AssertionError(f"missing owner {owner!r}")


def only_thread(
    document: dict[str, Any],
    update_id: str,
    owner: str,
) -> dict[str, Any]:
    """Return the only conversation on one update owner."""
    questions = update_owner(document, update_id, owner)["questions"]
    assert len(questions) == 1
    return questions[0]


def legacy_document() -> dict[str, Any]:
    """Return a valid document with the structured legacy current state."""
    document = {
        "title": "Legacy migration",
        "summary": "A document created before direct briefing publishing.",
        "current_state": copy.deepcopy(LEGACY_STATE),
        "updates": [],
    }
    return document


def thread(thread_id: str, anchor: str, text: str) -> dict[str, Any]:
    """Build one saved human-authored conversation."""
    return {
        "id": thread_id,
        "anchor": {"kind": "element", "path": anchor},
        "turns": [
            {
                "author": "human",
                "text": text,
                "at": "2026-08-04T11:05:00Z",
            }
        ],
    }


def test_publish_appends_one_direct_briefing_and_keeps_history(
    tmp_path: Path,
) -> None:
    """The same stable record moves from latest position into the ledger."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    original = copy.deepcopy(read_content_file(run_dir)["updates"])

    assert publish_command(root, None, briefing()) == 0

    saved = read_content_file(run_dir)
    assert "current_state" not in saved
    assert saved["updates"][:-1] == original
    assert saved["updates"][-1] == BRIEFING
    delivered = embedded_document(
        (run_dir / "index.html").read_text(encoding="utf-8")
    )
    assert delivered["updates"] == saved["updates"]

    for owner, anchor in (
        ("root", BRIEFING["id"]),
        ("delivery", f"{BRIEFING['id']}/delivery"),
        ("publisher", f"{BRIEFING['id']}/delivery/publisher"),
    ):
        queue_line(run_dir, f"Question for {owner}", anchor_id=anchor)
    assert fold_command(root, None) == 0
    before = read_content_file(run_dir)["updates"][-1]

    assert publish_command(
        root,
        None,
        briefing("second-briefing", "2026-08-04T13:00:00Z"),
    ) == 0

    later = read_content_file(run_dir)
    assert later["updates"][-2] == before
    assert later["updates"][-2]["id"] == BRIEFING["id"]
    assert later["updates"][-1]["id"] == "second-briefing"
    assert count_unanswered_questions(run_dir) == 3


@pytest.mark.parametrize("count", [1, 6])
def test_publish_accepts_one_to_six_flexible_sections(
    tmp_path: Path,
    count: int,
) -> None:
    """Both supported section-count boundaries are accepted."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    candidate = briefing()
    candidate["lanes"] = [
        {"id": f"section-{index}", "name": f"Section {index}", "items": []}
        for index in range(count)
    ]

    assert publish_command(root, None, candidate) == 0
    assert len(read_content_file(run_dir)["updates"][-1]["lanes"]) == count


@pytest.mark.parametrize("count", [0, 7])
def test_publish_rejects_section_counts_outside_range(
    tmp_path: Path,
    count: int,
) -> None:
    """An invalid section count changes no run bytes."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = briefing()
    candidate["lanes"] = [
        {"id": f"section-{index}", "name": f"Section {index}", "items": []}
        for index in range(count)
    ]

    with pytest.raises(CliError, match="lanes must contain 1 to 6 lanes"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(extra={}), "must have exactly"),
        (lambda value: value.pop("summary"), "missing summary"),
        (
            lambda value: value.update(headline="Build -> test -> ship"),
            "must not contain arrows",
        ),
        (
            lambda value: value.update(summary="Too short."),
            "at least four words",
        ),
    ],
)
def test_malformed_publish_payload_is_atomic(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    """Payload validation finishes before any run file is touched."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = briefing()
    mutate(candidate)

    with pytest.raises(CliError, match=message):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


def test_old_two_part_envelope_is_rejected_clearly(tmp_path: Path) -> None:
    """The removed current-state and changes contract is not accepted."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)

    with pytest.raises(
        CliError,
        match="no longer accepts the current_state plus changes envelope",
    ):
        publish_command(
            root,
            None,
            {"current_state": {}, "changes": {}},
        )

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize("level", ["root", "lane", "item", "nested"])
def test_agent_cannot_author_questions(tmp_path: Path, level: str) -> None:
    """Conversations remain tool-owned at every possible payload depth."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = briefing()
    owner: dict[str, Any] = candidate
    if level == "lane":
        owner = candidate["lanes"][0]
    elif level == "item":
        owner = candidate["lanes"][0]["items"][0]
    elif level == "nested":
        item = candidate["lanes"][0]["items"][0]
        item["forensics"] = [
            {
                "title": "Nested evidence",
                "body": "This note otherwise has valid content.",
                "questions": [],
            }
        ]
        owner = item["forensics"][0]
    owner["questions"] = []

    with pytest.raises(CliError, match="questions is tool-owned"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


def test_update_root_lane_and_item_chats_use_full_backend_lifecycle(
    tmp_path: Path,
) -> None:
    """Every briefing level folds, counts, projects, answers, and lints."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, briefing()) == 0
    anchors = {
        "root": BRIEFING["id"],
        "delivery": f"{BRIEFING['id']}/delivery",
        "publisher": f"{BRIEFING['id']}/delivery/publisher",
    }
    for owner, anchor in anchors.items():
        queue_line(run_dir, f"Question for {owner}", anchor_id=anchor)

    assert count_unanswered_questions(run_dir) == 3
    assert fold_command(root, None) == 0
    saved = read_content_file(run_dir)
    projected = project_document(saved)
    projected_update = projected["updates"][-1]
    assert len(projected_update["questions"]) == 1
    assert len(projected_update["lanes"][0]["questions"]) == 1
    assert len(projected_update["lanes"][0]["items"][0]["questions"]) == 1

    for owner in anchors:
        saved = read_content_file(run_dir)
        thread_id = only_thread(saved, BRIEFING["id"], owner)["id"]
        assert answer_command(root, None, thread_id, f"Answer for {owner}.") == 0

    assert count_unanswered_questions(run_dir) == 0
    assert lint_document(read_content_file(run_dir)) == []


def test_structured_current_state_archives_all_conversations(
    tmp_path: Path,
) -> None:
    """Root, lane, and item threads move to rewritten archived paths."""
    root = tmp_path / "runs"
    document = legacy_document()
    state = document["current_state"]
    owners = (
        (state, "legacy-root", CURRENT_STATE_ROOT),
        (
            state["lanes"][0],
            "legacy-lane-thread",
            current_state_lane_path("legacy-lane"),
        ),
        (
            state["lanes"][0]["items"][0],
            "legacy-item-thread",
            current_state_item_path("legacy-item"),
        ),
    )
    for owner, thread_id, anchor in owners:
        owner["questions"] = [thread(thread_id, anchor, f"Chat at {anchor}")]
    run_dir = make_run(root, document=document)

    assert publish_command(root, None, briefing()) == 0

    saved = read_content_file(run_dir)
    assert "current_state" not in saved
    archived = saved["updates"][-2]
    archive_id = archived["id"]
    expected = {
        "root": archive_id,
        "legacy-lane": f"{archive_id}/legacy-lane",
        "legacy-item": f"{archive_id}/legacy-lane/legacy-item",
    }
    for owner, path in expected.items():
        assert only_thread(saved, archive_id, owner)["anchor"]["path"] == path
    assert saved["legacy_anchor_aliases"] == {
        CURRENT_STATE_ROOT: archive_id,
        current_state_lane_path("legacy-lane"): expected["legacy-lane"],
        current_state_item_path("legacy-item"): expected["legacy-item"],
    }
    projected = project_document(saved)
    assert projected["legacy_anchor_aliases"] == saved[
        "legacy_anchor_aliases"
    ]

    for owner in expected:
        thread_id = only_thread(saved, archive_id, owner)["id"]
        assert answer_command(root, None, thread_id, "The archive still works.") == 0
    assert count_unanswered_questions(run_dir) == 0


@pytest.mark.parametrize(
    ("answer", "expected_count"),
    [("", 1), ("Answered before migration.", 0)],
)
def test_undated_legacy_pair_keeps_matching_queue_record_after_migration(
    tmp_path: Path,
    answer: str,
    expected_count: int,
) -> None:
    """Archiving cannot duplicate or resurrect an undated legacy pair."""
    root = tmp_path / "runs"
    question = "Does this legacy root chat survive migration?"
    document = legacy_document()
    document["current_state"]["questions"] = [
        {"question": question, "answer": answer}
    ]
    run_dir = make_run(root, document=document)
    record = queue_line(
        run_dir,
        question,
        anchor_id=CURRENT_STATE_ROOT,
        timestamp="2026-08-04T11:30:00Z",
    )

    assert publish_command(root, None, briefing()) == 0

    saved = read_content_file(run_dir)
    archive_id = saved["updates"][-2]["id"]
    migrated = only_thread(saved, archive_id, "root")
    assert migrated["turns"][0]["at"] == record["timestamp"]
    assert count_unanswered_questions(run_dir) == expected_count

    assert fold_command(root, None) == 0
    folded = read_content_file(run_dir)
    assert only_thread(folded, archive_id, "root") == migrated
    assert count_unanswered_questions(run_dir) == expected_count


@pytest.mark.parametrize(
    "old_anchor",
    [
        CURRENT_STATE_ROOT,
        current_state_lane_path("legacy-lane"),
        current_state_item_path("legacy-item"),
    ],
)
def test_unfolded_current_state_question_survives_migration(
    tmp_path: Path,
    old_anchor: str,
) -> None:
    """A queued question is merged and never targets a vanished anchor."""
    root = tmp_path / "runs"
    run_dir = make_run(root, document=legacy_document())
    record = queue_line(
        run_dir,
        "Please preserve this queued question.",
        anchor_id=old_anchor,
        timestamp="2026-08-04T11:30:00Z",
    )
    queue_before = (run_dir / "questions.jsonl").read_bytes()

    assert publish_command(root, None, briefing()) == 0

    saved = read_content_file(run_dir)
    archive_id = saved["updates"][-2]["id"]
    aliases = saved["legacy_anchor_aliases"]
    new_anchor = aliases[old_anchor]
    owner = "root"
    if old_anchor.endswith("/legacy-lane"):
        owner = "legacy-lane"
    elif old_anchor.endswith("/legacy-item"):
        owner = "legacy-item"
    migrated = only_thread(saved, archive_id, owner)
    assert migrated["anchor"]["path"] == new_anchor
    assert migrated["turns"][0]["text"] == record["text"]
    assert (run_dir / "questions.jsonl").read_bytes() == queue_before
    assert count_unanswered_questions(run_dir) == 1

    assert fold_command(root, None) == 0
    folded = read_content_file(run_dir)
    assert only_thread(folded, archive_id, owner) == migrated
    assert count_unanswered_questions(run_dir) == 1
    assert answer_command(
        root,
        None,
        migrated["id"],
        "This queued question survived migration.",
    ) == 0
    assert count_unanswered_questions(run_dir) == 0


def test_late_old_anchor_submission_uses_persistent_alias(tmp_path: Path) -> None:
    """A stale open page can submit after the atomic migration write."""
    root = tmp_path / "runs"
    run_dir = make_run(root, document=legacy_document())
    assert publish_command(root, None, briefing()) == 0
    saved = read_content_file(run_dir)
    archive_id = saved["updates"][-2]["id"]
    old_anchor = current_state_item_path("legacy-item")
    new_anchor = saved["legacy_anchor_aliases"][old_anchor]

    queue_line(
        run_dir,
        "This came from a stale page.",
        anchor_id=old_anchor,
        timestamp="2026-08-04T12:30:00Z",
    )
    assert count_unanswered_questions(run_dir) == 1
    assert fold_command(root, None) == 0

    folded = read_content_file(run_dir)
    migrated = only_thread(folded, archive_id, "legacy-item")
    assert migrated["anchor"]["path"] == new_anchor
    assert migrated["turns"][0]["text"] == "This came from a stale page."


def test_stale_follow_up_target_uses_persistent_alias(tmp_path: Path) -> None:
    """A stale page can reply to an archived thread through its old anchor."""
    root = tmp_path / "runs"
    old_anchor = current_state_item_path("legacy-item")
    document = legacy_document()
    item = document["current_state"]["lanes"][0]["items"][0]
    item["questions"] = [
        thread("q-legacy-item", old_anchor, "Can this thread move?")
    ]
    run_dir = make_run(root, document=document)
    assert publish_command(root, None, briefing()) == 0

    assert reply_target_error(run_dir, "q-legacy-item", old_anchor) is None


def test_four_claim_current_state_archives_once(tmp_path: Path) -> None:
    """The oldest shipped state shape becomes one ordinary briefing."""
    root = tmp_path / "runs"
    document = {
        "title": "Four claims",
        "summary": "A legacy document remains readable before migration.",
        "current_state": {
            "updated_at": "2026-08-04T10:00:00Z",
            "goal": "Keep the old document readable.",
            "focus": "Move it into ordinary history now.",
            "blocker": None,
            "next": "Publish the next complete briefing.",
        },
        "updates": [],
    }
    run_dir = make_run(root, document=document)

    assert publish_command(root, None, briefing()) == 0
    saved = read_content_file(run_dir)

    assert "current_state" not in saved
    assert [update["headline"] for update in saved["updates"]] == [
        "Archived legacy current state",
        BRIEFING["headline"],
    ]
    assert {
        item["id"] for item in saved["updates"][0]["lanes"][0]["items"]
    } == {"goal", "focus", "next"}

    assert publish_command(
        root,
        None,
        briefing("after-migration", "2026-08-04T13:00:00Z"),
    ) == 0
    later = read_content_file(run_dir)
    assert sum(
        update["headline"] == "Archived legacy current state"
        for update in later["updates"]
    ) == 1


def test_duplicate_id_does_not_start_legacy_migration(tmp_path: Path) -> None:
    """Identity rejection leaves current state and every run byte unchanged."""
    root = tmp_path / "runs"
    document = legacy_document()
    document["updates"].append(copy.deepcopy(BRIEFING))
    run_dir = make_run(root, document=document)
    before = run_bytes(run_dir)

    with pytest.raises(CliError, match="already exists"):
        publish_command(root, None, briefing())

    assert run_bytes(run_dir) == before


def test_candidate_document_validation_failure_is_atomic(
    tmp_path: Path,
) -> None:
    """A malformed saved legacy state is never partially archived."""
    root = tmp_path / "runs"
    document = legacy_document()
    document["current_state"]["lanes"][0]["items"][0]["trust"] = "invalid"
    run_dir = make_run(root)
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    (run_dir / "content.json").write_text(content, encoding="utf-8")
    before = run_bytes(run_dir)

    with pytest.raises(CliError, match="not a recognized trust chip"):
        publish_command(root, None, briefing())

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize("malformation", ["missing", "not-list"])
def test_malformed_structured_state_is_not_archived_as_four_claims(
    tmp_path: Path,
    malformation: str,
) -> None:
    """A damaged structured state fails without changing any run bytes."""
    root = tmp_path / "runs"
    document = legacy_document()
    if malformation == "missing":
        del document["current_state"]["lanes"]
    else:
        document["current_state"]["lanes"] = {}
    run_dir = make_run(root)
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    (run_dir / "content.json").write_text(content, encoding="utf-8")
    before = run_bytes(run_dir)

    with pytest.raises(CliError, match="current_state"):
        publish_command(root, None, briefing())

    assert run_bytes(run_dir) == before


def test_render_failure_during_migration_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renderer exception leaves legacy state and its queue untouched."""
    root = tmp_path / "runs"
    run_dir = make_run(root, document=legacy_document())
    queue_line(
        run_dir,
        "Keep this while rendering fails.",
        anchor_id=CURRENT_STATE_ROOT,
        timestamp="2026-08-04T11:30:00Z",
    )
    before = run_bytes(run_dir)

    def fail_render(run_dir: Path, data: Any) -> str:
        raise CliError("injected render failure")

    monkeypatch.setattr(runfiles, "render_html", fail_render)
    with pytest.raises(CliError, match="injected render failure"):
        publish_command(root, None, briefing())

    assert run_bytes(run_dir) == before


def test_write_failure_during_migration_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial replacement is rolled back byte for byte."""
    root = tmp_path / "runs"
    run_dir = make_run(root, document=legacy_document())
    before = run_bytes(run_dir)
    real_write = runfiles.write_text_atomic
    writes: list[str] = []

    def fail_second_write(path: Path, content: str) -> None:
        writes.append(path.name)
        if len(writes) == 2:
            raise OSError("injected index replacement failure")
        real_write(path, content)

    monkeypatch.setattr(runfiles, "write_text_atomic", fail_second_write)
    with pytest.raises(CliError, match="cannot write run"):
        publish_command(root, None, briefing())

    assert writes == ["content.json", "index.html"]
    assert run_bytes(run_dir) == before


def test_cli_reads_one_direct_object_from_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public CLI accepts the direct contract without an envelope."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(briefing())))

    assert main(["publish", "-"]) == 0
    assert read_content_file(run_dir)["updates"][-1] == BRIEFING


def test_new_run_starts_with_a_coherent_normal_document(tmp_path: Path) -> None:
    """Creation uses updates only and never invents a second model object."""
    root = tmp_path / "runs"

    assert new_command(root, "Initial briefing", "initial-briefing") == 0

    content = read_content_file(root / "initial-briefing")
    assert "current_state" not in content
    assert set(content) == {"title", "summary", "updates"}
    assert set(content["updates"][0]) == {
        "id",
        "timestamp",
        "headline",
        "summary",
        "lanes",
    }
    assert "visual-brief publish" in content["updates"][0]["summary"]
