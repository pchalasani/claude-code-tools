"""Tests for the claude -> codex direction of `aichat port`.

Uses real functions and real tmp files (no mocks), following the
conventions of the other test_port_* files.

E2E RESUME VERIFICATION EVIDENCE (mandatory manual check, run for
real against the user's actual homes -- see the feature contract):
a small real Claude session was ported into the real ~/.codex home
and resumed successfully with `codex exec resume`; the ported rollout
file and its history.jsonl entry were deleted afterwards. Exact
commands and output are recorded in the E2E EVIDENCE comment block
at the bottom of tests/test_port_claude_to_codex_cli.py (the CLI
tests were split into that file to respect the 1000-line limit).
"""

import datetime
import json
import re
import uuid as uuid_mod
from pathlib import Path
from typing import Any

import pytest

from claude_code_tools.port_claude_to_codex import (
    TOOL_TEXT_CAP,
    port_claude_session_to_codex,
)

CLAUDE_SID = "0bed5c0a-ef62-4446-9830-444fb2a9e001"

ROLLOUT_NAME_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.jsonl$"
)

ASSISTANT_MSG_ID_RE = re.compile(r"^msg_[0-9a-f]{50}$")

LONG_TOOL_ARGS = "z" * (TOOL_TEXT_CAP + 700)
LONG_TOOL_RESULT = "r" * (TOOL_TEXT_CAP + 300)

# Whitespace-sensitive genuine texts: leading/trailing spaces and
# newlines MUST survive the port verbatim (contract: normal message
# text is never altered, only tool args/results are capped).
WS_USER_TEXT = "    def f(x):\n        return x  \n"
WS_USER_BLOCK_TEXT = "\n  user block with edges  "
WS_ASST_BLOCK_TEXT = "  reply with\n    indented body\n"
WS_ASST_STR = "\n  bare assistant string  \n"

# Fixture tuple: (claude session path, new codex id, rollout path).
Ported = tuple[Path, str, Path]


def _ts(seconds: int) -> str:
    """Build a deterministic ISO timestamp for fixture lines."""
    return f"2026-07-16T17:39:{seconds:02d}.000Z"


def _line(
    seconds: int,
    line_type: str,
    content: Any,
    *,
    sidechain: bool = False,
    is_meta: bool = False,
    extra: dict[str, Any] | None = None,
) -> str:
    """Build one Claude session user/assistant JSONL line."""
    data: dict[str, Any] = {
        "parentUuid": None,
        "isSidechain": sidechain,
        "userType": "external",
        "cwd": "/tmp/ported-proj",
        "sessionId": CLAUDE_SID,
        "version": "2.1.211",
        "gitBranch": "feat/x",
        "type": line_type,
        "message": {"role": line_type, "content": content},
        "uuid": str(uuid_mod.uuid4()),
        "timestamp": _ts(seconds),
    }
    if is_meta:
        data["isMeta"] = True
    if extra:
        data.update(extra)
    return json.dumps(data)


def write_claude_session(
    claude_home: Path, project_dir: Path
) -> Path:
    """Write a synthetic Claude session exercising every rule."""
    proj = (
        claude_home
        / "projects"
        / str(project_dir).replace("/", "-").replace(".", "-")
    )
    proj.mkdir(parents=True, exist_ok=True)
    cwd = str(project_dir)
    lines = [
        # Claude-internal noise line types: all skipped.
        json.dumps(
            {
                "type": "custom-title",
                "customTitle": "NOISE-TITLE",
                "sessionId": CLAUDE_SID,
            }
        ),
        json.dumps(
            {
                "type": "agent-name",
                "agentName": "NOISE-AGENT",
                "sessionId": CLAUDE_SID,
            }
        ),
        json.dumps({"type": "mode", "mode": "normal"}),
        json.dumps(
            {
                "type": "permission-mode",
                "permissionMode": "bypassPermissions",
            }
        ),
        json.dumps(
            {
                "type": "file-history-snapshot",
                "messageId": "m1",
                "snapshot": {"trackedFileBackups": {}},
            }
        ),
        json.dumps(
            {
                "parentUuid": None,
                "isSidechain": False,
                "attachment": {"type": "deferred_tools_delta"},
                "type": "attachment",
            }
        ),
        json.dumps(
            {"type": "last-prompt", "lastPrompt": "NOISE-LASTPROMPT"}
        ),
        json.dumps(
            {"type": "system", "subtype": "stop_hook_summary"}
        ),
        json.dumps({"type": "summary", "summary": "NOISE-SUMMARY"}),
        # Hostile shapes: never crash, always skipped.
        "null",
        "[]",
        '"just a string"',
        "1",
        "{{{not valid json",
        json.dumps({"type": ["user"], "message": "x"}),
        json.dumps({"type": "user", "message": None}),
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": None}}
        ),
        # Command wrappers: skipped.
        _line(
            0,
            "user",
            "<command-name>/clear</command-name>\n"
            "<command-message>NOISE-CMD</command-message>",
        ),
        _line(
            0,
            "user",
            "<local-command-stdout>NOISE-STDOUT"
            "</local-command-stdout>",
        ),
        _line(0, "user", "<bash-input>NOISE-BASH</bash-input>"),
        # System-reminder block: skipped.
        _line(
            1,
            "user",
            [
                {
                    "type": "text",
                    "text": "<system-reminder>NOISE-REMINDER"
                    "</system-reminder>",
                }
            ],
        ),
        # Teammate/agent notifications recorded as type=user lines
        # (ground-truth shape: plain string content, not typed by the
        # user): skipped.
        _line(
            1,
            "user",
            "Another Claude session sent a message:\n"
            '<teammate-message teammate_id="cc-research" color="blue">\n'
            "NOISE-TEAMMATE\n"
            "</teammate-message>\n\n"
            "This came from another Claude session — not typed by "
            "your user.",
        ),
        _line(
            1,
            "user",
            '<teammate-message teammate_id="cc-x">NOISE-TEAMMATE2'
            "</teammate-message>",
        ),
        # isMeta user line: skipped.
        _line(1, "user", "Caveat: NOISE-CAVEAT", is_meta=True),
        # Sidechain lines: skipped.
        _line(1, "user", "NOISE-SIDECHAIN-USER", sidechain=True),
        _line(
            1,
            "assistant",
            [{"type": "text", "text": "NOISE-SIDECHAIN-ASST"}],
            sidechain=True,
        ),
        # Genuine transcript.
        _line(2, "user", "Hello, please compute X"),
        _line(
            3,
            "assistant",
            [
                {
                    "type": "thinking",
                    "thinking": "SECRET-THINKING",
                    "signature": "sig",
                },
                {"type": "text", "text": "Sure, computing."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": LONG_TOOL_ARGS},
                },
            ],
        ),
        _line(
            4,
            "user",
            [
                {
                    "tool_use_id": "toolu_1",
                    "type": "tool_result",
                    "content": LONG_TOOL_RESULT,
                    "is_error": False,
                }
            ],
        ),
        _line(
            5,
            "assistant",
            [
                {"type": "redacted_thinking", "data": "SECRET-RT"},
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "Read",
                    "input": {"file_path": "/tmp/f.txt"},
                },
            ],
        ),
        # Tool result carrying genuine output PLUS an appended
        # system-reminder block (the common real-Claude shape): the
        # reminder must be stripped, the genuine output kept.
        _line(
            6,
            "user",
            [
                {
                    "tool_use_id": "toolu_2",
                    "type": "tool_result",
                    "content": [
                        {"type": "text", "text": "file contents here"},
                        {
                            "type": "text",
                            "text": "<system-reminder>NOISE-TR-REM"
                            "</system-reminder>",
                        },
                    ],
                }
            ],
        ),
        # Empty-content lines: dropped, never emitted as empty items.
        _line(6, "user", "   "),
        _line(6, "assistant", [{"type": "text", "text": ""}]),
        _line(7, "user", "Thanks, now do Y"),
        _line(
            8,
            "assistant",
            [{"type": "text", "text": "Done with Y"}],
        ),
        # Whitespace-sensitive genuine texts: preserved verbatim.
        _line(9, "user", WS_USER_TEXT),
        _line(
            9,
            "user",
            [{"type": "text", "text": WS_USER_BLOCK_TEXT}],
        ),
        _line(
            10,
            "assistant",
            [{"type": "text", "text": WS_ASST_BLOCK_TEXT}],
        ),
        _line(10, "assistant", WS_ASST_STR),
    ]
    # Rewrite cwd fields to the tmp project dir.
    fixed: list[str] = []
    for raw in lines:
        fixed.append(raw.replace("/tmp/ported-proj", cwd))
    path = proj / f"{CLAUDE_SID}.jsonl"
    path.write_text("\n".join(fixed) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "myproj"
    d.mkdir()
    return d


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    d = tmp_path / "claude-home"
    d.mkdir()
    return d


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    d = tmp_path / "codex-home"
    d.mkdir()
    return d


def _read_lines(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _item_pairs(out_path: Path) -> list[tuple[str, str]]:
    """Extract (role, text) pairs from a rollout's response items."""
    return [
        (
            item["payload"]["role"],
            item["payload"]["content"][0]["text"],
        )
        for item in _read_lines(out_path)[1:]
    ]


class TestConverter:
    """Direct tests of port_claude_session_to_codex."""

    @pytest.fixture
    def ported(
        self,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> Ported:
        session = write_claude_session(claude_home, project_dir)
        new_id, out_path = port_claude_session_to_codex(
            session, codex_home=codex_home
        )
        return session, new_id, out_path

    def test_rollout_placement_and_filename(
        self,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        session = write_claude_session(claude_home, project_dir)
        date_before = datetime.date.today()
        new_id, out_path = port_claude_session_to_codex(
            session, codex_home=codex_home
        )
        date_after = datetime.date.today()
        # placed under <codex_home>/sessions/YYYY/MM/DD where the
        # date is TODAY's local date (both bounds tolerated in case
        # the port straddled midnight)
        rel = out_path.relative_to(codex_home / "sessions")
        year, month, day, name = rel.parts
        allowed = {
            (f"{d.year:04d}", f"{d.month:02d}", f"{d.day:02d}")
            for d in (date_before, date_after)
        }
        assert (year, month, day) in allowed
        m = ROLLOUT_NAME_RE.match(name)
        assert m, name
        # the filename's date prefix matches the directory date
        assert name.startswith(f"rollout-{year}-{month}-{day}T")
        # filename UUID equals the returned session id
        assert m.group(1) == new_id

    def test_new_id_is_codex_style_uuid7(self, ported: Ported) -> None:
        _, new_id, _ = ported
        parsed = uuid_mod.UUID(new_id)
        assert parsed.version == 7
        assert str(parsed) == new_id

    def test_session_meta_shape_and_lineage(
        self, ported: Ported
    ) -> None:
        """session_meta payload matches the real ROOT rollout shape.

        Ground truth (real modern root rollouts, thread_source
        "user"): the payload always carries BOTH `session_id` and
        `id`, equal for a non-forked session, plus timestamp, cwd,
        originator and cli_version.
        """
        session, new_id, out_path = ported
        first = _read_lines(out_path)[0]
        assert first["type"] == "session_meta"
        assert isinstance(first["timestamp"], str)
        payload = first["payload"]
        # required root-rollout payload keys are all present
        assert set(payload.keys()) >= {
            "session_id",
            "id",
            "timestamp",
            "cwd",
            "originator",
            "cli_version",
        }
        assert payload["id"] == new_id
        # a ported session is a fresh (non-forked) root thread
        assert payload["session_id"] == new_id
        assert isinstance(payload["timestamp"], str)
        assert payload["cwd"] == json.loads(
            session.read_text().splitlines()[-1]
        ).get("cwd")
        assert payload["git"] == {"branch": "feat/x"}
        cm = first["continue_metadata"]
        assert cm["ported_from"] == "claude"
        assert cm["parent_session_id"] == CLAUDE_SID
        assert cm["parent_session_file"] == str(session.absolute())
        assert isinstance(cm["continued_at"], str)

    def test_message_item_shapes_match_real_rollouts(
        self, ported: Ported
    ) -> None:
        """Synthesized items match real modern rollout shapes exactly.

        Ground truth (rollout-2026-07-16T20-41-57-019f6d85-...):
        user payloads carry exactly {type, role, content,
        internal_chat_message_metadata_passthrough}; assistant
        payloads carry exactly {type, id, role, content, phase,
        internal_chat_message_metadata_passthrough} with a
        msg_<50 hex> id and a known phase. Every passthrough is
        {"turn_id": <UUIDv7>}, and the items of one turn (a user
        message plus the assistant items that follow it) share the
        same turn id, as in real rollouts.
        """
        _, _, out_path = ported
        items = _read_lines(out_path)[1:]
        assert items, "no response items written"
        prev_role = None
        prev_turn_id = None
        for item in items:
            assert set(item.keys()) == {
                "timestamp",
                "type",
                "payload",
            }
            assert item["type"] == "response_item"
            assert isinstance(item["timestamp"], str)
            payload = item["payload"]
            assert payload["type"] == "message"
            role = payload["role"]
            content = payload["content"]
            assert isinstance(content, list) and len(content) == 1
            block = content[0]
            assert set(block.keys()) == {"type", "text"}
            assert isinstance(block["text"], str)
            assert block["text"].strip()
            if role == "user":
                assert set(payload.keys()) == {
                    "type",
                    "role",
                    "content",
                    "internal_chat_message_metadata_passthrough",
                }
                assert block["type"] == "input_text"
            else:
                assert role == "assistant"
                assert set(payload.keys()) == {
                    "type",
                    "id",
                    "role",
                    "content",
                    "phase",
                    "internal_chat_message_metadata_passthrough",
                }
                assert block["type"] == "output_text"
                assert ASSISTANT_MSG_ID_RE.match(payload["id"])
                assert payload["phase"] in (
                    "commentary",
                    "final_answer",
                )
            passthrough = payload[
                "internal_chat_message_metadata_passthrough"
            ]
            assert set(passthrough.keys()) == {"turn_id"}
            turn_id = uuid_mod.UUID(passthrough["turn_id"])
            assert turn_id.version == 7
            assert str(turn_id) == passthrough["turn_id"]
            # assistant items continue the turn their user message
            # opened; a user message after an assistant one starts a
            # fresh turn
            if prev_role is not None:
                if role == "assistant":
                    assert passthrough["turn_id"] == prev_turn_id
                elif prev_role == "assistant":
                    assert passthrough["turn_id"] != prev_turn_id
            prev_role = role
            prev_turn_id = passthrough["turn_id"]

    def test_noise_and_sidechain_skipped(self, ported: Ported) -> None:
        _, _, out_path = ported
        raw = out_path.read_text(encoding="utf-8")
        for noise in (
            "NOISE-TITLE",
            "NOISE-AGENT",
            "NOISE-LASTPROMPT",
            "NOISE-SUMMARY",
            "NOISE-CMD",
            "NOISE-STDOUT",
            "NOISE-BASH",
            "NOISE-REMINDER",
            "NOISE-TEAMMATE",
            "NOISE-TEAMMATE2",
            "NOISE-TR-REM",
            "NOISE-CAVEAT",
            "NOISE-SIDECHAIN-USER",
            "NOISE-SIDECHAIN-ASST",
            "<command-name>",
            "<local-command-stdout>",
            "<bash-input>",
            "<system-reminder>",
            "<teammate-message",
            "Another Claude session sent a message",
        ):
            assert noise not in raw, noise

    def test_thinking_dropped(self, ported: Ported) -> None:
        _, _, out_path = ported
        raw = out_path.read_text(encoding="utf-8")
        assert "SECRET-THINKING" not in raw
        assert "SECRET-RT" not in raw

    def test_tool_use_flattened_and_truncated(
        self, ported: Ported
    ) -> None:
        _, _, out_path = ported
        items = _read_lines(out_path)[1:]
        texts = [
            i["payload"]["content"][0]["text"] for i in items
        ]
        calls = [
            t for t in texts if t.startswith("[claude tool call] ")
        ]
        assert len(calls) == 2
        assert calls[0].startswith("[claude tool call] Bash(")
        assert calls[1].startswith("[claude tool call] Read(")
        # tool args are capped with the explicit suffix
        assert re.search(r"\.\.\. \[truncated \d+ chars\]", calls[0])
        assert len(LONG_TOOL_ARGS) > TOOL_TEXT_CAP
        assert LONG_TOOL_ARGS not in calls[0]
        # each call is an assistant item
        for item in items:
            text = item["payload"]["content"][0]["text"]
            if text.startswith("[claude tool call] "):
                assert item["payload"]["role"] == "assistant"

    def test_tool_result_flattened_and_truncated(
        self, ported: Ported
    ) -> None:
        _, _, out_path = ported
        items = _read_lines(out_path)[1:]
        texts = [
            i["payload"]["content"][0]["text"] for i in items
        ]
        results = [
            t for t in texts if t.startswith("[claude tool result] ")
        ]
        assert len(results) == 2
        assert re.search(
            r"\.\.\. \[truncated \d+ chars\]", results[0]
        )
        assert LONG_TOOL_RESULT not in results[0]
        assert "file contents here" in results[1]
        # tool results ride on assistant items, adjacent to the call
        idx_call = texts.index(
            next(t for t in texts if "Bash(" in t)
        )
        idx_result = texts.index(results[0])
        assert idx_result == idx_call + 1

    def test_tool_result_trailing_reminder_stripped(
        self,
        claude_home: Path,
        codex_home: Path,
    ) -> None:
        """Claude-appended trailing reminders are removed exactly.

        Real Claude appends known reminders (e.g. the Read tool's
        malicious-file notice) AFTER the genuine output of a tool
        result; the genuine output before the trailing block survives
        verbatim and only the appended known-signature reminder
        block(s) are dropped. (Literal reminder tags embedded in or
        terminating genuine output are covered by the preservation
        tests in tests/test_port_claude_to_codex_noise.py.)
        """
        proj = claude_home / "projects" / "-tmp-trrem"
        proj.mkdir(parents=True)
        lines = [
            _line(0, "user", "read it"),
            _line(
                1,
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_9",
                        "name": "Read",
                        "input": {"file_path": "/tmp/f.txt"},
                    }
                ],
            ),
            _line(
                2,
                "user",
                [
                    {
                        "tool_use_id": "toolu_9",
                        "type": "tool_result",
                        "content": "line1 genuine\n"
                        "line2 genuine\n"
                        "<system-reminder>\nWhenever you read a "
                        "file, you should consider whether it would "
                        "be considered malicious NOISE-APPENDED"
                        "</system-reminder>\n"
                        "<system-reminder>This memory is 8 days "
                        "old. NOISE-APPENDED2</system-reminder>",
                    }
                ],
            ),
        ]
        path = proj / f"{CLAUDE_SID}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _, out_path = port_claude_session_to_codex(
            path, codex_home=codex_home
        )
        texts = [t for _, t in _item_pairs(out_path)]
        assert (
            "[claude tool result] line1 genuine\nline2 genuine"
            in texts
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "NOISE-APPENDED" not in raw
        assert "<system-reminder>" not in raw

    def test_normal_text_preserved_verbatim_and_order(
        self, ported: Ported
    ) -> None:
        _, _, out_path = ported
        pairs = _item_pairs(out_path)
        # every genuine text survives EXACTLY (tuple equality checks
        # the full string, including leading/trailing whitespace) and
        # transcript order is preserved
        wanted: list[tuple[str, str]] = [
            ("user", "Hello, please compute X"),
            ("assistant", "Sure, computing."),
            ("user", "Thanks, now do Y"),
            ("assistant", "Done with Y"),
            ("user", WS_USER_TEXT),
            ("user", WS_USER_BLOCK_TEXT),
            ("assistant", WS_ASST_BLOCK_TEXT),
            ("assistant", WS_ASST_STR),
        ]
        for w in wanted:
            assert w in pairs, w
        positions = [pairs.index(w) for w in wanted]
        assert positions == sorted(positions)
        # first item is a user message
        assert pairs[0][0] == "user"

    def test_no_empty_messages(self, ported: Ported) -> None:
        _, _, out_path = ported
        for item in _read_lines(out_path)[1:]:
            assert item["payload"]["content"][0]["text"].strip()

    def test_history_jsonl_appended(
        self, ported: Ported, codex_home: Path
    ) -> None:
        _, new_id, _ = ported
        history = codex_home / "history.jsonl"
        assert history.exists()
        entries = _read_lines(history)
        assert entries[-1]["session_id"] == new_id
        assert entries[-1]["text"] == "Hello, please compute X"
        assert isinstance(entries[-1]["ts"], int)

    def test_history_append_failure_removes_rollout(
        self,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        """A failed history append never leaves a rollout behind.

        Otherwise a retry after the surfaced error would create
        duplicate discoverable sessions.
        """
        session = write_claude_session(claude_home, project_dir)
        # make the history append fail: history.jsonl is a directory
        (codex_home / "history.jsonl").mkdir()
        with pytest.raises(OSError):
            port_claude_session_to_codex(
                session, codex_home=codex_home
            )
        leftovers = list(
            (codex_home / "sessions").rglob("rollout-*.jsonl")
        )
        assert leftovers == []

    def test_leading_assistant_gets_synthetic_user_opener(
        self,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        proj = claude_home / "projects" / "-tmp-lead"
        proj.mkdir(parents=True)
        lines = [
            _line(
                0,
                "assistant",
                [{"type": "text", "text": "I start talking"}],
            )
        ]
        path = proj / f"{CLAUDE_SID}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _, out_path = port_claude_session_to_codex(
            path, codex_home=codex_home
        )
        items = _read_lines(out_path)[1:]
        assert items[0]["payload"]["role"] == "user"
        assert (
            f"[Transcript ported from Claude Code session {CLAUDE_SID}]"
            in items[0]["payload"]["content"][0]["text"]
        )
        assert items[1]["payload"]["role"] == "assistant"

    def test_empty_session_raises_value_error(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        proj = claude_home / "projects" / "-tmp-empty"
        proj.mkdir(parents=True)
        path = proj / f"{CLAUDE_SID}.jsonl"
        path.write_text(
            json.dumps({"type": "summary", "summary": "x"}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="No portable messages"):
            port_claude_session_to_codex(path, codex_home=codex_home)
        # nothing was written
        sessions_dir = codex_home / "sessions"
        if sessions_dir.exists():
            assert not list(sessions_dir.rglob("*.jsonl"))
        assert not (codex_home / "history.jsonl").exists()

    def test_missing_file_raises(
        self, codex_home: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            port_claude_session_to_codex(
                tmp_path / "nope.jsonl", codex_home=codex_home
            )

    def test_hostile_shapes_do_not_crash(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        proj = claude_home / "projects" / "-tmp-hostile"
        proj.mkdir(parents=True)
        huge = json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "big " * 50000},
                "timestamp": _ts(0),
            }
        )
        lines = [
            "null",
            '{"n": ' + "1" * 5000 + "}",
            json.dumps({"type": "user"}),
            json.dumps({"type": "user", "message": []}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            None,
                            42,
                            {"type": "tool_use"},
                            {"type": "text", "text": None},
                        ],
                    },
                }
            ),
            huge,
        ]
        path = proj / f"{CLAUDE_SID}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        new_id, out_path = port_claude_session_to_codex(
            path, codex_home=codex_home
        )
        texts = [t for _, t in _item_pairs(out_path)]
        # the nameless tool_use flattens with a placeholder name
        assert any(
            t.startswith("[claude tool call] unknown(") for t in texts
        )
        # the huge genuine message survives verbatim (no cap)
        assert any(len(t) >= 4 * 50000 - 1 for t in texts)

    def test_hostile_block_type_values_do_not_crash(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Non-string block `type` values (list/dict/null) are skipped.

        A frozenset membership test on an unhashable JSON value would
        raise TypeError and abort the whole port.
        """
        proj = claude_home / "projects" / "-tmp-btype"
        proj.mkdir(parents=True)
        lines = [
            _line(
                0,
                "user",
                [
                    {"type": ["tool_result"], "content": "IGNORED-U"},
                    {"type": {"k": 1}, "text": "IGNORED-U2"},
                    {"type": None, "text": "IGNORED-U3"},
                    {"type": "text", "text": "genuine user text"},
                ],
            ),
            _line(
                1,
                "assistant",
                [
                    {"type": ["thinking"], "thinking": "IGNORED-A"},
                    {"type": {"k": 2}, "text": "IGNORED-A2"},
                    {"type": None, "text": "IGNORED-A3"},
                    {"type": "text", "text": "genuine reply"},
                ],
            ),
        ]
        path = proj / f"{CLAUDE_SID}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _, out_path = port_claude_session_to_codex(
            path, codex_home=codex_home
        )
        pairs = _item_pairs(out_path)
        assert ("user", "genuine user text") in pairs
        assert ("assistant", "genuine reply") in pairs
        raw = out_path.read_text(encoding="utf-8")
        assert "IGNORED-" not in raw

    def test_cwd_fallback_to_current_dir(
        self,
        claude_home: Path,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        proj = claude_home / "projects" / "-tmp-nocwd"
        proj.mkdir(parents=True)
        line = {
            "type": "user",
            "message": {"role": "user", "content": "hi there"},
            "timestamp": _ts(0),
        }
        path = proj / f"{CLAUDE_SID}.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        _, out_path = port_claude_session_to_codex(
            path, codex_home=codex_home
        )
        first = _read_lines(out_path)[0]
        assert first["payload"]["cwd"] == str(workdir)
