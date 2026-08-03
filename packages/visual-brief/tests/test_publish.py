"""Atomic detailed-current-state and dated-change publishing."""

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
from visual_brief.schema import (
    CURRENT_STATE_ROOT,
    current_state_item_path,
    current_state_lane_path,
)
from visual_brief.server.counting import count_unanswered_questions
from visual_brief.server.queue import MAX_QUESTION_LENGTH
from visual_brief.writes import (
    CliError,
    answer_command,
    fold_command,
    lint_document,
    publish_command,
)
from visual_brief.writes import runfiles

CHANGE = {
    "id": "detailed-state-contract",
    "timestamp": "2026-08-01T12:00:00Z",
    "headline": "Publishing now carries detailed state and changes together",
    "summary": "The structured snapshot changes while history remains.",
    "lanes": [],
}

STATE = {
    "headline": "The detailed publishing contract is active",
    "summary": "The current position is organized into addressable details.",
    "lanes": [
        {
            "id": "delivery",
            "name": "What works now",
            "items": [
                {
                    "id": "parser",
                    "glance": "The structured state parser is working.",
                    "explanation": (
                        "It validates the same visible content as dated items."
                    ),
                    "trust": "verified-by-me",
                    "forensics": ["focused parser check: passed"],
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
                    "explanation": "A cold reviewer must inspect the final tree.",
                    "trust": "reported-by-agent",
                }
            ],
        },
    ],
}

_STATE_LANE_NAMES = (
    "Delivered behavior",
    "Remaining risks",
    "Decisions needed",
    "User-visible effects",
    "Evidence worth opening",
    "Next verification",
    "Deferred work",
)


def payload() -> dict[str, Any]:
    """Return an independent valid publish envelope."""
    return {
        "current_state": copy.deepcopy(STATE),
        "changes": copy.deepcopy(CHANGE),
    }


def state_lanes(count: int) -> list[dict[str, Any]]:
    """Return a valid set of context-specific state lanes."""
    return [
        {
            "id": f"section-{index + 1}",
            "name": _STATE_LANE_NAMES[index],
            "items": [],
        }
        for index in range(count)
    ]


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


def state_owner(document: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Return one structured-state root, lane, or globally identified item."""
    state = document["current_state"]
    if owner_id == "root":
        return state
    for lane in state["lanes"]:
        if lane["id"] == owner_id:
            return lane
        for item in lane["items"]:
            if item["id"] == owner_id:
                return item
    raise AssertionError(f"missing state owner {owner_id}")


def only_thread(document: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Return the only tool-owned conversation at one state owner."""
    questions = state_owner(document, owner_id)["questions"]
    assert len(questions) == 1
    return questions[0]


def test_publish_replaces_detailed_state_and_appends_matching_change(
    tmp_path: Path,
) -> None:
    """One transaction saves the snapshot and immutable dated change."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    old_updates = copy.deepcopy(read_content_file(run_dir)["updates"])

    assert publish_command(root, None, payload()) == 0

    saved = read_content_file(run_dir)
    assert saved["current_state"] == {
        "updated_at": CHANGE["timestamp"],
        **STATE,
    }
    assert saved["updates"][:-1] == old_updates
    assert saved["updates"][-1] == CHANGE
    delivered = embedded_document(
        (run_dir / "index.html").read_text(encoding="utf-8")
    )
    assert delivered["current_state"] == saved["current_state"]
    assert delivered["updates"] == saved["updates"]

    later = payload()
    later["current_state"]["headline"] = (
        "The detailed publishing contract is verified"
    )
    later["changes"]["id"] = "contract-verified"
    later["changes"]["timestamp"] = "2026-08-01T13:00:00Z"

    assert publish_command(root, None, later) == 0

    replaced = read_content_file(run_dir)
    assert replaced["current_state"]["headline"] == (
        later["current_state"]["headline"]
    )
    assert replaced["current_state"]["updated_at"] == (
        "2026-08-01T13:00:00Z"
    )
    assert replaced["updates"][:-1] == saved["updates"]


def test_publish_rolls_back_when_second_atomic_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page replacement failure leaves the run retryable and unchanged."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
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
        publish_command(root, None, payload())

    assert writes == ["content.json", "index.html"]
    assert run_bytes(run_dir) == before

    monkeypatch.setattr(runfiles, "write_text_atomic", real_write)
    assert publish_command(root, None, payload()) == 0
    assert read_content_file(run_dir)["updates"][-1] == CHANGE


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(extra={}), "publish payload must have"),
        (
            lambda value: value["current_state"].update(extra="No extras."),
            "current_state must have exactly",
        ),
        (
            lambda value: value["current_state"].update(
                headline="Build -> test -> ship"
            ),
            "must not contain arrows",
        ),
        (
            lambda value: value["current_state"].update(
                summary="Build/test/ship."
            ),
            "must not contain a status chain",
        ),
        (
            lambda value: value["current_state"].update(summary="Too short."),
            "at least four words",
        ),
        (
            lambda value: value["current_state"].update(
                summary="This summary has enough words"
            ),
            "sentence punctuation",
        ),
    ],
)
def test_malformed_or_cryptic_state_changes_no_run_byte(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    """Mechanical state failures are rejected before any file is replaced."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = payload()
    mutate(candidate)

    with pytest.raises(CliError, match=message):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize("count", [1, 6])
def test_publish_accepts_one_to_six_state_sections(
    tmp_path: Path,
    count: int,
) -> None:
    """The publish boundary accepts both ends of the section range."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    candidate = payload()
    candidate["current_state"]["lanes"] = state_lanes(count)

    assert publish_command(root, None, candidate) == 0
    assert len(read_content_file(run_dir)["current_state"]["lanes"]) == count


@pytest.mark.parametrize("count", [0, 7])
def test_publish_rejects_state_section_counts_outside_range(
    tmp_path: Path,
    count: int,
) -> None:
    """A rejected section count leaves every run file unchanged."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = payload()
    candidate["current_state"]["lanes"] = state_lanes(count)

    with pytest.raises(
        CliError,
        match="current_state.lanes must contain 1 to 6 sections",
    ):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize("level", ["root", "lane", "item"])
def test_agent_cannot_submit_state_conversations(
    tmp_path: Path,
    level: str,
) -> None:
    """Every state conversation is added by queue folding, never payload JSON."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = payload()
    owner = candidate["current_state"]
    if level == "lane":
        owner = owner["lanes"][0]
    elif level == "item":
        owner = owner["lanes"][0]["items"][0]
    owner["questions"] = []

    with pytest.raises(CliError, match="questions is tool-owned"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


def test_agent_cannot_hide_questions_in_nested_forensic_note(
    tmp_path: Path,
) -> None:
    """Agent-authored conversations are forbidden throughout current state."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = payload()
    item = candidate["current_state"]["lanes"][0]["items"][0]
    item["forensics"] = [
        {
            "title": "Outer evidence",
            "body": "The outer forensic note is valid.",
            "children": [
                {
                    "title": "Hidden conversation",
                    "body": "The nested forensic note is otherwise valid.",
                    "questions": [],
                }
            ],
        }
    ]

    with pytest.raises(
        CliError,
        match=r"current_state\.lanes\[0\]\.items\[0\]\.forensics\[0\]"
        r"\.children\[0\]\.questions is tool-owned",
    ):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


def test_moving_state_item_keeps_its_anchor_and_conversation(
    tmp_path: Path,
) -> None:
    """An item id, rather than its lane slot, owns its row and chat identity."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    anchor = current_state_item_path("parser")
    queue_line(run_dir, "Does the parser move safely?", anchor_id=anchor)
    assert fold_command(root, None) == 0
    before_thread = copy.deepcopy(only_thread(read_content_file(run_dir), "parser"))

    moved = payload()
    parser = moved["current_state"]["lanes"][0]["items"].pop()
    moved["current_state"]["lanes"][1]["items"].append(parser)
    moved["changes"]["id"] = "parser-moved"
    moved["changes"]["timestamp"] = "2026-08-01T13:00:00Z"
    assert publish_command(root, None, moved) == 0

    saved = read_content_file(run_dir)
    parser_owner = state_owner(saved, "parser")
    assert parser_owner["questions"] == [before_thread]
    assert only_thread(saved, "parser")["anchor"]["path"] == anchor


@pytest.mark.parametrize(
    ("owner_id", "anchor", "remove"),
    [
        (
            "delivery",
            current_state_lane_path("delivery"),
            lambda state: state["lanes"].pop(0),
        ),
        (
            "parser",
            current_state_item_path("parser"),
            lambda state: state["lanes"][0]["items"].pop(0),
        ),
    ],
)
def test_publish_rejects_removing_a_state_owner_with_conversations(
    tmp_path: Path,
    owner_id: str,
    anchor: str,
    remove: Callable[[dict[str, Any]], object],
) -> None:
    """A replacement cannot silently discard a lane or item's chat."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    queue_line(run_dir, f"Question at {owner_id}", anchor_id=anchor)
    assert fold_command(root, None) == 0
    before = run_bytes(run_dir)
    candidate = payload()
    remove(candidate["current_state"])
    candidate["changes"]["id"] = f"remove-{owner_id}"

    with pytest.raises(CliError, match="owns conversations"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize(
    ("owner_id", "anchor", "remove_or_rename"),
    [
        (
            "delivery",
            current_state_lane_path("delivery"),
            lambda state: state["lanes"].pop(0),
        ),
        (
            "parser",
            current_state_item_path("parser"),
            lambda state: state["lanes"][0]["items"][0].update(
                id="renamed-parser"
            ),
        ),
    ],
)
def test_publish_rejects_orphaning_an_unfolded_state_question(
    tmp_path: Path,
    owner_id: str,
    anchor: str,
    remove_or_rename: Callable[[dict[str, Any]], object],
) -> None:
    """A queued question prevents removal before any run file changes."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    queue_line(run_dir, f"Queued question at {owner_id}", anchor_id=anchor)
    before = run_bytes(run_dir)
    candidate = payload()
    remove_or_rename(candidate["current_state"])
    candidate["changes"]["id"] = f"orphan-{owner_id}"

    with pytest.raises(CliError, match="unmatched queued question"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


@pytest.mark.parametrize(
    ("parent_case", "expected_count"),
    [("missing", 0), ("wrong-anchor", 1)],
)
def test_invalid_queued_reply_does_not_block_state_owner_removal(
    tmp_path: Path,
    parent_case: str,
    expected_count: int,
) -> None:
    """Ignore replies whose parent is absent or belongs to another anchor."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    if parent_case == "missing":
        parent_id = "q-does-not-exist"
    else:
        other_anchor = current_state_item_path("review")
        queue_line(run_dir, "Question for review", anchor_id=other_anchor)
        assert fold_command(root, None) == 0
        parent_id = only_thread(
            read_content_file(run_dir), "review"
        )["id"]
    queue_line(
        run_dir,
        "Invalid reply for parser",
        anchor_id=current_state_item_path("parser"),
        parent_id=parent_id,
    )
    assert count_unanswered_questions(run_dir) == expected_count
    candidate = payload()
    candidate["current_state"]["lanes"][0]["items"].pop(0)
    candidate["changes"]["id"] = f"remove-parser-with-{parent_case}-reply"

    assert publish_command(root, None, candidate) == 0


def test_overlong_queued_question_does_not_block_state_owner_removal(
    tmp_path: Path,
) -> None:
    """Ignore queue text that the server counting path rejects."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    record = {
        "type": "question",
        "anchor_id": current_state_item_path("parser"),
        "text": "x" * (MAX_QUESTION_LENGTH + 1),
    }
    (run_dir / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )
    candidate = payload()
    candidate["current_state"]["lanes"][0]["items"].pop(0)
    candidate["changes"]["id"] = "remove-parser-with-invalid-queue-text"

    assert publish_command(root, None, candidate) == 0


@pytest.mark.parametrize(
    "anchor",
    ["now/state/tests", current_state_item_path("already-absent")],
)
def test_other_unfolded_queue_anchors_do_not_block_state_owner_removal(
    tmp_path: Path,
    anchor: str,
) -> None:
    """Update and already-stale anchors do not constrain current state."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    queue_line(run_dir, "This question has another owner.", anchor_id=anchor)
    candidate = payload()
    candidate["current_state"]["lanes"][0]["items"].pop(0)
    candidate["changes"]["id"] = "remove-unrelated-parser"

    assert publish_command(root, None, candidate) == 0

    saved = read_content_file(run_dir)
    assert all(
        item["id"] != "parser"
        for lane in saved["current_state"]["lanes"]
        for item in lane["items"]
    )


def test_root_lane_and_item_conversations_are_carried_forward(
    tmp_path: Path,
) -> None:
    """All three stable state owner levels keep their tool-owned chats."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    for owner, anchor in (
        ("root", CURRENT_STATE_ROOT),
        ("delivery", current_state_lane_path("delivery")),
        ("parser", current_state_item_path("parser")),
    ):
        queue_line(run_dir, f"Question for {owner}", anchor_id=anchor)
    assert fold_command(root, None) == 0
    old = read_content_file(run_dir)
    expected = {
        owner: copy.deepcopy(state_owner(old, owner)["questions"])
        for owner in ("root", "delivery", "parser")
    }

    later = payload()
    later["changes"]["id"] = "state-refreshed"
    later["changes"]["timestamp"] = "2026-08-01T13:00:00Z"
    assert publish_command(root, None, later) == 0

    saved = read_content_file(run_dir)
    for owner, questions in expected.items():
        assert state_owner(saved, owner)["questions"] == questions


def test_state_conversations_fold_answer_count_and_lint_normally(
    tmp_path: Path,
) -> None:
    """State chat uses the same backend lifecycle as dated-update chat."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    anchor = current_state_item_path("parser")
    queue_line(run_dir, "What is still uncertain?", anchor_id=anchor)
    assert count_unanswered_questions(run_dir) == 1
    assert fold_command(root, None) == 0
    thread_id = only_thread(read_content_file(run_dir), "parser")["id"]
    assert answer_command(root, None, thread_id, "Only final review remains.") == 0
    assert count_unanswered_questions(run_dir) == 0

    document = read_content_file(run_dir)
    state_owner(document, "parser")["explanation"] = (
        "Three checks remain: 1. types, 2. tests, 3. review."
    )
    warnings = lint_document(document)
    assert any(anchor in warning and "enumeration" in warning for warning in warnings)


def test_duplicate_thread_check_includes_current_state(tmp_path: Path) -> None:
    """One thread id cannot name both a state chat and an update chat."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    anchor = current_state_item_path("parser")
    queue_line(run_dir, "Can this thread collide?", anchor_id=anchor)
    assert fold_command(root, None) == 0
    thread = copy.deepcopy(only_thread(read_content_file(run_dir), "parser"))
    before = run_bytes(run_dir)
    candidate = payload()
    candidate["changes"]["id"] = "duplicate-chat"
    thread["anchor"]["path"] = "duplicate-chat/changed/copy"
    candidate["changes"]["lanes"] = [
        {
            "id": "changed",
            "name": "What changed",
            "items": [
                {
                    "id": "copy",
                    "glance": "This duplicate must be rejected.",
                    "explanation": "A thread id has exactly one owner.",
                    "trust": "known-limitation",
                    "questions": [thread],
                }
            ],
        }
    ]

    with pytest.raises(CliError, match="two conversations carry the id"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


def test_duplicate_change_does_not_replace_existing_state(
    tmp_path: Path,
) -> None:
    """History identity is checked before candidate state is installed."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert publish_command(root, None, payload()) == 0
    before = run_bytes(run_dir)
    duplicate = payload()
    duplicate["current_state"]["headline"] = (
        "This replacement must never reach storage"
    )

    with pytest.raises(CliError, match="already exists"):
        publish_command(root, None, duplicate)

    assert run_bytes(run_dir) == before


def test_state_item_ids_are_unique_across_all_lanes(tmp_path: Path) -> None:
    """Global item identity prevents move-stable anchors from colliding."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = run_bytes(run_dir)
    candidate = payload()
    duplicate = copy.deepcopy(candidate["current_state"]["lanes"][0]["items"][0])
    candidate["current_state"]["lanes"][1]["items"].append(duplicate)

    with pytest.raises(CliError, match="unique across every state lane"):
        publish_command(root, None, candidate)

    assert run_bytes(run_dir) == before


def test_document_without_state_gains_structured_state_on_first_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document without current state remains compatible."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    assert "current_state" not in read_content_file(run_dir)
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload())))

    assert main(["publish", "-"]) == 0

    assert read_content_file(run_dir)["current_state"]["headline"] == (
        STATE["headline"]
    )


def test_legacy_four_claim_state_is_replaced_by_structured_publish(
    tmp_path: Path,
) -> None:
    """The shipped legacy state remains readable until normal replacement."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    legacy = read_content_file(run_dir)
    legacy["current_state"] = {
        "updated_at": "2026-08-01T10:00:00Z",
        "goal": "Keep the existing legacy state readable.",
        "focus": "The compatibility path remains active now.",
        "blocker": None,
        "next": "Replace it with one structured publish.",
    }
    write_content(run_dir, legacy)

    assert publish_command(root, None, payload()) == 0

    saved = read_content_file(run_dir)["current_state"]
    assert saved["headline"] == STATE["headline"]
    assert "goal" not in saved


def test_new_run_has_a_valid_structured_initial_state(tmp_path: Path) -> None:
    """New runs start with detailed-state schema and no invented chat."""
    root = tmp_path / "runs"

    assert new_command(root, "Initial state", "initial-state") == 0

    run_dir = root / "initial-state"
    content = read_content_file(run_dir)
    metadata = json.loads(
        (run_dir / "meta.json").read_text(encoding="utf-8")
    )
    state = content["current_state"]
    assert state["updated_at"] == metadata["created_at"]
    assert set(state) == {"updated_at", "headline", "summary", "lanes"}
    assert "visual-brief publish" in content["updates"][0]["summary"]
