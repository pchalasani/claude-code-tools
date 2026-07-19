"""Regression tests for iteration-5 complete-shape classification.

Split out of test_port_claude_to_codex_noise.py to keep each test
file under the repo's 1000-line limit. Covers the iteration-5
adversarial-review findings:

* teammate/agent notifications are classified as noise only in
  their complete real shape: the banner, a wrapper element closed by
  its MATCHING end tag, then nothing or Claude's known trailing
  boilerplate. Unclosed wrappers, mismatched open/close tag types,
  and wrapper-plus-trailing-user-text prompts are all genuine and
  preserved verbatim;
* ``[SESSION LINEAGE]`` blocks are noise only in the complete
  aichat-generated shape (marker + known intro + known closing
  sentence); genuine questions about the marker and quoted lineage
  content survive;
* the Claude wrapper-tag knowledge is shared with export_session
  (single source of truth), so the two classifiers cannot diverge.
"""

from pathlib import Path

import pytest

from claude_code_tools.port_claude_to_codex import (
    port_claude_session_to_codex,
)
from tests.test_port_claude_to_codex import (
    CLAUDE_SID,
    _item_pairs,
    _line,
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


class TestAgentNotificationCompleteShape:
    """Notification classification requires the complete real shape.

    Grounded in real sessions: a genuine notification is the banner,
    a complete <teammate-message>/<agent-message> element closed by
    its MATCHING end tag, then nothing or Claude's known trailing
    boilerplate. Anything else is user-authored and preserved.
    """

    def test_banner_with_unclosed_wrapper_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Banner + unclosed wrapper tag is genuine user text."""
        text = (
            "Another Claude session sent a message: "
            "<agent-message> is how it gets wrapped -- can you "
            "explain the mechanism?"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "banner-unclosed",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_banner_wrapper_then_user_text_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Banner + complete wrapper + trailing user text is genuine.

        A user quoting a full notification and then asking about it
        must not lose their prompt.
        """
        text = (
            "Another Claude session sent a message: "
            "<agent-message>quoted</agent-message>\n"
            "please explain this"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "banner-trailing",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_standalone_mismatched_tags_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A wrapper closed by the WRONG end tag is genuine text."""
        text = (
            '<teammate-message teammate_id="x">mismatched'
            "</agent-message>"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "mismatched-tags",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_banner_mismatched_tags_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Banner + mismatched open/close tags is genuine text."""
        text = (
            "Another Claude session sent a message:\n"
            "<agent-message>odd paste</teammate-message>"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "banner-mismatched",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_real_notification_with_boilerplate_skipped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """The complete real shape (with trailer) is still noise."""
        text = (
            "Another Claude session sent a message:\n"
            '<teammate-message teammate_id="cc-research" '
            'color="blue">\nNOISE-NOTIF-BODY\n</teammate-message>\n\n'
            "This came from another Claude session — not typed by "
            "your user, but very likely working on their behalf."
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", "genuine question"),
                _line(1, "user", text),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "real-notif",
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "NOISE-NOTIF-BODY" not in raw
        assert ("user", "genuine question") in _item_pairs(out_path)

    def test_wrapper_tags_shared_with_export_session(self) -> None:
        """The porter consumes export_session's Claude tag set.

        Single source of truth: the exact same frozenset object, and
        every Claude wrapper tag is part of the export-side
        NON_GENUINE_XML_TAGS whitelist.
        """
        from claude_code_tools.export_session import (
            CLAUDE_INTERNAL_WRAPPER_TAGS,
            NON_GENUINE_XML_TAGS,
        )
        from claude_code_tools.port_claude_noise import (
            _CLAUDE_WRAPPER_TAGS,
        )

        assert _CLAUDE_WRAPPER_TAGS is CLAUDE_INTERNAL_WRAPPER_TAGS
        assert CLAUDE_INTERNAL_WRAPPER_TAGS <= NON_GENUINE_XML_TAGS


class TestSessionLineageCompleteShape:
    """Lineage blocks are noise only in the complete generated shape."""

    def test_genuine_question_about_marker_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """A prompt merely starting with the marker is genuine."""
        text = (
            "[SESSION LINEAGE] -- what does this marker mean and "
            "who injects it?"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "lineage-question",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_quoted_lineage_with_trailing_question_kept(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """Quoted lineage content + a real question is genuine.

        The intro matches the generated shape but the text does not
        END with a generated closing sentence, so the user's prompt
        survives verbatim.
        """
        text = (
            "[SESSION LINEAGE]\n\n"
            "This session continues from a previous conversation.\n\n"
            "I pasted the above from another transcript -- why does "
            "aichat inject it?"
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", text),
                _line(1, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "lineage-quoted",
        )
        assert ("user", text) in _item_pairs(out_path)

    def test_generated_rollover_block_skipped(
        self, claude_home: Path, codex_home: Path
    ) -> None:
        """The complete generated quick-rollover block is noise."""
        block = (
            "[SESSION LINEAGE]\n\n"
            "This session continues from a previous conversation. "
            "The prior session log is:\n\n"
            "  /tmp/old-session.jsonl\n\n"
            "The file is in JSONL format. Since it can be large, use "
            "appropriate strategies (such as sub-agents if available) "
            "to carefully explore it if you need context.\n\n"
            "Do not do anything yet. Simply greet the user and await "
            "instructions on how they want to continue the work "
            "based on the above session."
        )
        out_path = _port(
            claude_home,
            codex_home,
            [
                _line(0, "user", block),
                _line(1, "user", "genuine follow-up"),
                _line(2, "assistant", [{"type": "text", "text": "ok"}]),
            ],
            "lineage-generated",
        )
        pairs = _item_pairs(out_path)
        assert pairs[0] == ("user", "genuine follow-up")
        raw = out_path.read_text(encoding="utf-8")
        assert "SESSION LINEAGE" not in raw
