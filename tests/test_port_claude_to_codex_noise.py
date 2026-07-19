"""Regression tests for claude -> codex noise/reminder handling.

Split out of test_port_claude_to_codex.py to keep each test file
under the repo's 1000-line limit. Covers the adversarial-review
findings of iterations 2 and 3:

* real Claude-internal ``<task-notification>`` and
  ``<local-command-stderr>`` user lines (non-sidechain, non-isMeta)
  are skipped;
* ``<system-reminder>`` handling is provenance-based (grounded in a
  corpus scan of real sessions): injected reminders always occupy an
  ENTIRE user string / text block / tool-result item, so only
  pure-reminder texts are dropped; genuine text that quotes,
  contains, or even ends with the literal tag is preserved verbatim;
  the one injected shape mixed into genuine tool output -- a
  terminal reminder whose body matches a known signature -- is
  peeled off the end;
* the history.jsonl append is failure-atomic and tail-safe: a
  mid-write failure truncates the partial fragment away and removes
  the published rollout, a retry then succeeds cleanly, and an
  unterminated pre-existing tail is separated so the new entry
  always starts its own line.

No mocks: the partial-write failure is produced with a real
``RLIMIT_FSIZE`` file-size limit.
"""

import json
import os
import resource
import signal
from pathlib import Path

import pytest

from claude_code_tools.port_claude_to_codex import (
    _append_history_transactional,
    _rollback_history_append,
    port_claude_session_to_codex,
)
from claude_code_tools.port_codex_flatten import (
    TOOL_TEXT_CAP,
    _stringify_tool_value,
)
from tests.test_port_claude_to_codex import (
    CLAUDE_SID,
    _item_pairs,
    _line,
)

# Real ground-truth shape (from an actual session): a completed
# background-task notification recorded as a plain type=user line.
TASK_NOTIFICATION_TEXT = (
    "<task-notification>\n"
    "<task-id>af304eebbab90ce62</task-id>\n"
    "<tool-use-id>toolu_01GSGdyD</tool-use-id>\n"
    "<output-file>/tmp/tasks/af304eebbab90ce62.output</output-file>\n"
    "<status>completed</status>\n"
    "<summary>Agent NOISE-TASK-NOTIF finished</summary>\n"
    "<result>NOISE-TASK-RESULT</result>\n"
    "<usage><subagent_tokens>143766</subagent_tokens></usage>\n"
    "</task-notification>"
)

# Real ground-truth shape: stderr of a local command run by Claude.
LOCAL_STDERR_TEXT = (
    "<local-command-stderr>Error: NOISE-LOCAL-STDERR"
    "</local-command-stderr>"
)


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


def _port(
    claude_home: Path,
    codex_home: Path,
    lines: list[str],
    name: str,
) -> Path:
    """Write a session with the given lines and port it."""
    proj = claude_home / "projects" / f"-tmp-{name}"
    proj.mkdir(parents=True)
    path = proj / f"{CLAUDE_SID}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _, out_path = port_claude_session_to_codex(
        path, codex_home=codex_home
    )
    return out_path


class TestInternalWrapperLines:
    """Non-sidechain, non-isMeta Claude wrapper user lines skipped."""

    def test_task_notification_skipped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A real <task-notification> user line is internal noise.

        Real sessions record these as plain type=user string lines
        with neither isSidechain nor isMeta set; they were never
        typed by the user and must not become Codex user messages.
        """
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "genuine question"),
                _line(1, "user", TASK_NOTIFICATION_TEXT),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "tasknotif",
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "NOISE-TASK-NOTIF" not in raw
        assert "NOISE-TASK-RESULT" not in raw
        assert "task-notification" not in raw
        assert ("user", "genuine question") in _item_pairs(out_path)

    def test_local_command_stderr_skipped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A real <local-command-stderr> user line is internal noise."""
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "genuine question"),
                _line(1, "user", LOCAL_STDERR_TEXT),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "localstderr",
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "NOISE-LOCAL-STDERR" not in raw
        assert "local-command-stderr" not in raw
        assert ("user", "genuine question") in _item_pairs(out_path)


class TestUserReminderProvenance:
    """Pure injected reminders dropped; genuine text kept verbatim."""

    def test_injected_reminder_block_dropped_genuine_block_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """The real injected shape: a reminder as its OWN text block.

        Claude records injected reminders as separate whole text
        blocks alongside the genuine typed block; the reminder block
        goes, the genuine block survives.
        """
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(
                    0,
                    "user",
                    [
                        {
                            "type": "text",
                            "text": "<system-reminder>NOISE-INJECTED"
                            "</system-reminder>",
                        },
                        {
                            "type": "text",
                            "text": "the actual typed question",
                        },
                    ],
                ),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "injected-block",
        )
        pairs = _item_pairs(out_path)
        assert ("user", "the actual typed question") in pairs
        raw = out_path.read_text(encoding="utf-8")
        assert "NOISE-INJECTED" not in raw
        assert "<system-reminder>" not in raw

    def test_stacked_pure_reminder_string_dropped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A string of several stacked reminder blocks emits nothing."""
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(
                    0,
                    "user",
                    "<system-reminder>NOISE-PURE1</system-reminder>\n"
                    "<system-reminder>NOISE-PURE2</system-reminder>",
                ),
                _line(1, "user", "genuine follow-up"),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "stacked-rem",
        )
        pairs = _item_pairs(out_path)
        assert pairs[0] == ("user", "genuine follow-up")
        assert "NOISE-PURE" not in out_path.read_text(encoding="utf-8")

    def test_user_text_quoting_reminder_preserved_verbatim(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A genuine prompt QUOTING a well-formed reminder is kept.

        Injected reminders always occupy an entire string/block of
        their own, so a mixed text -- genuine words around a
        well-formed reminder region -- is user-authored (e.g. a user
        pasting or asking about a reminder) and must survive with
        nothing excised.
        """
        text = (
            "Why does Claude show me\n"
            "<system-reminder>the file has been modified"
            "</system-reminder>\n"
            "after I edit files?"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "quoted-str",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_user_block_quoting_reminder_preserved_verbatim(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Same preservation applies to list-form user text blocks."""
        text = (
            "before text\n"
            "<system-reminder>quoted reminder body"
            "</system-reminder>\n"
            "after text"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", [{"type": "text", "text": text}]),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "quoted-block",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_pure_reminder_message_still_skipped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A message that is ONLY a reminder block emits nothing."""
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(
                    0,
                    "user",
                    "<system-reminder>NOISE-PURE</system-reminder>",
                ),
                _line(1, "user", "genuine follow-up"),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "pure-rem",
        )
        pairs = _item_pairs(out_path)
        assert pairs[0] == ("user", "genuine follow-up")
        assert "NOISE-PURE" not in out_path.read_text(encoding="utf-8")

    def test_unclosed_reminder_tag_is_genuine_user_text(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """User-authored text starting with the bare tag is kept.

        Without a closing tag there is no well-formed injected block,
        so the message is genuine (e.g. a user asking about the tag)
        and must survive verbatim.
        """
        text = (
            "<system-reminder> is the tag Claude uses -- "
            "what does it mean?"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "unclosed-rem",
        )
        assert ("user", text) in _item_pairs(out_path)


class TestToolOutputReminderPreservation:
    """Literal reminder tags inside genuine tool output preserved."""

    def test_mid_output_literal_reminder_preserved(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Reading a file that CONTAINS reminder tags loses nothing.

        Only the Claude-appended trailing block with a KNOWN injected
        signature (here the Read tool's malicious-file notice) is
        peeled; the literal well-formed block inside the read source
        content is genuine transcript content and survives verbatim.
        """
        source = (
            "def f():\n"
            '    return "<system-reminder>LITERAL-IN-FILE'
            '</system-reminder>"\n'
            "# end of file"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "read the fixture file"),
                _line(
                    1,
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "id": "toolu_5",
                            "name": "Read",
                            "input": {"file_path": "/tmp/fixture.py"},
                        }
                    ],
                ),
                _line(
                    2,
                    "user",
                    [
                        {
                            "tool_use_id": "toolu_5",
                            "type": "tool_result",
                            "content": source
                            + "\n<system-reminder>\nWhenever you "
                            "read a file, you should consider "
                            "whether it would be considered "
                            "malicious NOISE-APPENDED"
                            "</system-reminder>",
                        }
                    ],
                ),
            ],
            "mid-literal",
        )
        texts = [t for _, t in _item_pairs(out_path)]
        assert f"[claude tool result] {source}" in texts
        raw = out_path.read_text(encoding="utf-8")
        assert "LITERAL-IN-FILE" in raw
        assert "NOISE-APPENDED" not in raw

    def test_eof_literal_reminder_preserved(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Genuine output ENDING with a literal reminder is kept.

        A file whose final content is a reminder tag produces a tool
        result terminating in a well-formed block with an
        unrecognized body; that terminal block is ambiguous, so the
        whole output survives verbatim (nothing truncated).
        """
        output = (
            "# transcript fixture\n"
            "<system-reminder>EOF-LITERAL-BODY</system-reminder>"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "cat the fixture"),
                _line(
                    1,
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "id": "toolu_7",
                            "name": "Bash",
                            "input": {"command": "cat fixture.txt"},
                        }
                    ],
                ),
                _line(
                    2,
                    "user",
                    [
                        {
                            "tool_use_id": "toolu_7",
                            "type": "tool_result",
                            "content": output,
                        }
                    ],
                ),
            ],
            "eof-literal",
        )
        texts = [t for _, t in _item_pairs(out_path)]
        assert f"[claude tool result] {output}" in texts
        raw = out_path.read_text(encoding="utf-8")
        assert "EOF-LITERAL-BODY" in raw

    def test_mid_output_reminder_without_trailing_block(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Output whose ONLY reminder sits mid-content is untouched."""
        output = (
            "grep hit 1: <system-reminder>SESSION-JSON-LITERAL"
            "</system-reminder>\n"
            "grep hit 2: plain line"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "grep the transcripts"),
                _line(
                    1,
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "id": "toolu_6",
                            "name": "Bash",
                            "input": {"command": "grep -r reminder"},
                        }
                    ],
                ),
                _line(
                    2,
                    "user",
                    [
                        {
                            "tool_use_id": "toolu_6",
                            "type": "tool_result",
                            "content": output,
                        }
                    ],
                ),
            ],
            "mid-only",
        )
        texts = [t for _, t in _item_pairs(out_path)]
        assert f"[claude tool result] {output}" in texts


class TestGenuinePromptsNotMisclassified:
    """Genuine prompts starting with suspicious text are preserved.

    Noise classification must validate COMPLETE known wrapper shapes
    (record-level provenance -- isMeta, isSidechain -- handles the
    rest); ordinary user content is never dropped merely because it
    begins with a generic phrase or a recognized tag.
    """

    def test_caveat_prefixed_prompt_without_ismeta_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A typed prompt starting "Caveat:" is genuine user input.

        Real injected caveat banners carry isMeta=true (and the
        <local-command-caveat> wrapper); a plain user line has
        neither, so the text prefix alone must not discard it.
        """
        text = "Caveat: do not change the public API while fixing this"
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "caveat-genuine",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_caveat_only_session_ports(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A session whose ONLY prompt starts "Caveat:" still ports."""
        text = "Caveat: this repo uses tabs, keep them"
        out_path = _port(
            claude_home,
            codex_home,
            [_line(0, "user", text)],
            "caveat-only",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_agent_banner_without_wrapper_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """The banner phrase alone does not make a prompt noise.

        Real notifications are the banner IMMEDIATELY followed by a
        <teammate-message>/<agent-message> element; a user typing the
        phrase with ordinary text after it is genuine.
        """
        text = (
            "Another Claude session sent a message: what does that "
            "mean exactly? Please explain."
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "banner-genuine",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_unclosed_wrapper_tag_prompt_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Text merely BEGINNING with a known tag is genuine."""
        text = "<command-name> is what Claude records -- why?"
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "unclosed-wrapper",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_wrapper_block_followed_by_question_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A complete wrapper QUOTED before genuine text is kept.

        Only lines consisting of nothing but wrapper blocks are
        Claude-recorded noise; trailing genuine text means the user
        pasted the wrapper into their own prompt.
        """
        text = (
            "<local-command-stdout>some output</local-command-stdout>"
            "\nwhy did my command print this?"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "wrapper-question",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_real_command_wrapper_line_still_skipped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """The real multi-block command wrapper shape stays noise."""
        wrapper = (
            "<command-name>/clear</command-name>\n"
            "            <command-message>NOISE-CMD</command-message>\n"
            "            <command-args></command-args>"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "genuine question"),
                _line(1, "user", wrapper),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "real-wrapper",
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "NOISE-CMD" not in raw
        assert "command-name" not in raw
        assert ("user", "genuine question") in _item_pairs(out_path)


def _max_parseable_depth() -> int:
    """Find a deep nesting level this interpreter's json.loads accepts."""
    for depth in (4000, 2000, 992, 496, 240):
        try:
            json.loads("[" * depth + "1" + "]" * depth)
            return depth
        except RecursionError:
            continue
    return 100


class TestDeeplyNestedToolValues:
    """Parseable-but-hostile deep nesting never crashes conversion."""

    def test_stringify_deep_value_never_raises(self) -> None:
        """A structure too deep for the JSON encoder still renders.

        Built programmatically (50k levels), this always exceeds the
        encoder's recursion budget; the helper must return a capped
        string instead of propagating RecursionError.
        """
        deep: object = 1
        for _ in range(50000):
            deep = [deep]
        text = _stringify_tool_value(deep, TOOL_TEXT_CAP)
        assert isinstance(text, str)
        assert "nesting too deep" in text
        assert len(text) <= TOOL_TEXT_CAP + 100

    def test_deeply_nested_tool_args_and_results_port(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Deep tool_use.input / tool_result.content pass end-to-end.

        json.loads accepts nesting far deeper than
        JSONEncoder.iterencode can re-emit, so a hostile session line
        can be parseable yet crash a naive re-serialization. The
        depth is probed at runtime so the fixture line is parseable
        by THIS interpreter by construction.
        """
        depth = _max_parseable_depth()
        deep_json = "[" * depth + '"DEEP-CORE"' + "]" * depth
        line_tmpl = (
            '{{"type": "{t}", "timestamp": "2026-07-16T17:39:0{s}.000Z",'
            ' "sessionId": "%s", "cwd": "/tmp/deep-proj",'
            ' "message": {{"role": "{t}", "content": {c}}}}}'
        ) % CLAUDE_SID
        asst_content = (
            '[{"type": "tool_use", "id": "toolu_d", "name": "Bash",'
            ' "input": ' + deep_json + "}]"
        )
        user_content = (
            '[{"type": "tool_result", "tool_use_id": "toolu_d",'
            ' "content": ' + deep_json + "}]"
        )
        lines = [
            line_tmpl.format(t="user", s=0, c='"port the deep session"'),
            line_tmpl.format(t="assistant", s=1, c=asst_content),
            line_tmpl.format(t="user", s=2, c=user_content),
        ]
        # premise: every fixture line IS parseable by this interpreter
        for raw in lines:
            assert isinstance(json.loads(raw), dict)
        out_path = _port(
            claude_home, codex_home, lines, "deep-nesting"
        )
        pairs = _item_pairs(out_path)
        assert ("user", "port the deep session") in pairs
        texts = [t for _, t in pairs]
        assert any(
            t.startswith("[claude tool call] Bash(") for t in texts
        )
        assert any(
            t.startswith("[claude tool result] ") for t in texts
        )
        # every emitted text stays capped (plus label/suffix slack)
        for t in texts:
            assert len(t) <= TOOL_TEXT_CAP + 200


class TestConcurrentHistoryRollback:
    """Failure rollback never destroys a concurrent writer's bytes."""

    def _run_rollback(
        self, path: Path, original: bytes, entry: bytes
    ) -> None:
        fd = os.open(str(path), os.O_RDWR)
        try:
            _rollback_history_append(fd, len(original), entry)
        finally:
            os.close(fd)

    def test_own_partial_entry_truncated(self, tmp_path: Path) -> None:
        """The tail matching our own partial write is removed."""
        history = tmp_path / "history.jsonl"
        original = b'{"session_id": "old"}\n'
        entry = b'{"session_id": "new", "ts": 1, "text": "hi"}\n'
        history.write_bytes(original + entry[:17])
        self._run_rollback(history, original, entry)
        assert history.read_bytes() == original

    def test_full_entry_without_concurrent_data_truncated(
        self, tmp_path: Path
    ) -> None:
        """A completely written entry (e.g. fsync failed) rolls back."""
        history = tmp_path / "history.jsonl"
        original = b'{"session_id": "old"}\n'
        entry = b'{"session_id": "new", "ts": 1, "text": "hi"}\n'
        history.write_bytes(original + entry)
        self._run_rollback(history, original, entry)
        assert history.read_bytes() == original

    def test_concurrent_append_never_truncated(
        self, tmp_path: Path
    ) -> None:
        """Foreign bytes after the recorded size are left untouched.

        flock is advisory and Codex appends without taking it: when
        the region past the pre-append size is not exactly our own
        (partial) entry, truncating would destroy the concurrent
        writer's history entry, so the file must be left as-is.
        """
        history = tmp_path / "history.jsonl"
        original = b'{"session_id": "old"}\n'
        foreign = b'{"session_id": "codex-live", "ts": 2}\n'
        entry = b'{"session_id": "new", "ts": 1, "text": "hi"}\n'
        content = original + foreign + entry[:10]
        history.write_bytes(content)
        self._run_rollback(history, original, entry)
        assert history.read_bytes() == content

    def test_interleaved_after_own_partial_never_truncated(
        self, tmp_path: Path
    ) -> None:
        """Foreign bytes interleaved after our fragment also block it."""
        history = tmp_path / "history.jsonl"
        original = b'{"session_id": "old"}\n'
        foreign = b'{"session_id": "codex-live", "ts": 2}\n'
        entry = b'{"session_id": "new", "ts": 1, "text": "hi"}\n'
        content = original + entry[:10] + foreign
        history.write_bytes(content)
        self._run_rollback(history, original, entry)
        assert history.read_bytes() == content


class TestHistoryAppendTransactional:
    """history.jsonl append is atomic under mid-write failure."""

    def test_partial_append_truncated_and_rollout_removed(
        self, codex_home: Path
    ) -> None:
        """A failure AFTER partial bytes leaves no history fragment.

        A real RLIMIT_FSIZE limit lets the first few bytes of the
        entry hit the disk before the write fails, exercising the
        truncate-back path: the pre-existing history content must
        survive byte-for-byte, the fragment must be gone, the rollout
        must be removed, and a retry must append one clean entry.
        """
        if not hasattr(signal, "SIGXFSZ"):
            pytest.skip("requires SIGXFSZ/RLIMIT_FSIZE")
        history = codex_home / "history.jsonl"
        original = (
            json.dumps(
                {"session_id": "old-id", "ts": 1, "text": "old entry"}
            )
            + "\n"
        )
        history.write_text(original, encoding="utf-8")
        rollout = codex_home / "rollout-fake.jsonl"
        rollout.write_text("{}\n", encoding="utf-8")
        original_size = len(original.encode("utf-8"))
        old_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        try:
            # allow a few bytes of the new entry, then fail (EFBIG)
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (original_size + 5, hard)
            )
            with pytest.raises(OSError):
                _append_history_transactional(
                    codex_home, "new-id", "x" * 200, rollout
                )
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
            signal.signal(signal.SIGXFSZ, old_handler)
        # no fragment: history restored byte-for-byte
        assert history.read_text(encoding="utf-8") == original
        # published rollout removed so a retry cannot duplicate it
        assert not rollout.exists()
        # retry (limits restored) appends exactly one complete entry
        rollout.write_text("{}\n", encoding="utf-8")
        _append_history_transactional(
            codex_home, "new-id", "retry text", rollout
        )
        lines = history.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["session_id"] == "old-id"
        retried = json.loads(lines[1])
        assert retried["session_id"] == "new-id"
        assert retried["text"] == "retry text"
        assert rollout.exists()

    def test_unterminated_complete_record_separated(
        self, codex_home: Path
    ) -> None:
        """A complete final record missing its newline is separated.

        An interrupted writer can leave a valid JSON object without a
        trailing newline; the new entry must start its own line so
        BOTH records stay parseable and discoverable.
        """
        history = codex_home / "history.jsonl"
        old = json.dumps(
            {"session_id": "old-id", "ts": 1, "text": "old entry"}
        )
        history.write_text(old, encoding="utf-8")  # no newline
        rollout = codex_home / "rollout-fake.jsonl"
        rollout.write_text("{}\n", encoding="utf-8")
        _append_history_transactional(
            codex_home, "new-id", "new text", rollout
        )
        lines = history.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["session_id"] == "old-id"
        assert json.loads(lines[1])["session_id"] == "new-id"

    def test_unterminated_fragment_isolated(
        self, codex_home: Path
    ) -> None:
        """An incomplete JSON fragment never absorbs the new entry.

        The malformed fragment is left on a line of its own (line
        parsers skip it) and the appended entry is a clean,
        discoverable line.
        """
        history = codex_home / "history.jsonl"
        fragment = '{"session_id": "trunc'
        history.write_text(fragment, encoding="utf-8")
        rollout = codex_home / "rollout-fake.jsonl"
        rollout.write_text("{}\n", encoding="utf-8")
        _append_history_transactional(
            codex_home, "new-id", "new text", rollout
        )
        lines = history.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert lines[0] == fragment
        entry = json.loads(lines[1])
        assert entry["session_id"] == "new-id"
        assert entry["text"] == "new text"
