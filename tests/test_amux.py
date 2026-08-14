"""Tests for amux detection, rendering, and caching.

The detection heuristics are the fragile part -- they read harness UIs that
change between releases -- so they are tested against captured screen text
rather than a live tmux server.
"""

from __future__ import annotations

import json

import pytest

from claude_code_tools.amux import cache, detect, render
from claude_code_tools.amux.model import Agent

CLAUDE_IDLE = """
  I've committed the change and pushed the branch.
────────────────────────────────────────────── certify-main ──
❯
──────────────────────────────────────────────────────────────
   fable   observability.feat-certify-codex  feat/certify-codex
  ctx ████████░░ 86%   5h ░░░░░░░░░░ 3% ↻4h29m
  ⏵⏵ bypass permissions on · ← 1 agent
"""

CLAUDE_BG = CLAUDE_IDLE.replace("· ← 1 agent", "· 1 monitor · ← 1 agent")

CLAUDE_BUSY = """
  Reading the config file now.
✻ Cooked for 14m 17s · esc to interrupt
  ctx ████░░░░░░ 44%
"""

CLAUDE_ASKING = """
  I can either rebase or merge. Which do you prefer?
❯ 1. Rebase onto main
  2. Merge main in
  3. Skip for now
"""

CODEX_IDLE = """
• Explored the repository layout.
─ Worked for 2m 04s ─────────────────────────────────
› Write tests for @filename
  gpt-5.6-sol high · proposalwriter · main · Context 55% used
"""

CODEX_BUSY = """
• Waiting for background terminal (1m 02s • esc to interrupt)
  gpt-5.6-sol xhigh · observability · main
"""

CODEX_ASKING = """
• I found two candidate configs.
  Should I delete the stale one before continuing?
›
  gpt-5.6-sol high · farchat · main
"""


class TestClassifyArgv:
    def test_detects_claude(self) -> None:
        argv = "claude --dangerously-skip-permissions --resume certify"
        assert detect.classify_argv(argv) == "claude"

    def test_detects_codex(self) -> None:
        argv = "node /path/@openai/codex/bin/codex.js --yolo"
        assert detect.classify_argv(argv) == "codex"

    def test_plain_shell_is_not_an_agent(self) -> None:
        assert detect.classify_argv("vim README.md") is None

    def test_empty_argv(self) -> None:
        assert detect.classify_argv("") is None


class TestDetectState:
    @pytest.mark.parametrize(
        "screen,kind,expected",
        [
            (CLAUDE_ASKING, "claude", "input"),
            (CLAUDE_BUSY, "claude", "busy"),
            (CLAUDE_BG, "claude", "bg"),
            (CLAUDE_IDLE, "claude", "idle"),
            (CODEX_ASKING, "codex", "input"),
            (CODEX_BUSY, "codex", "busy"),
            (CODEX_IDLE, "codex", "idle"),
        ],
    )
    def test_states(self, screen: str, kind: str, expected: str) -> None:
        assert detect.detect_state(screen, kind) == expected  # type: ignore[arg-type]

    def test_input_outranks_busy(self) -> None:
        """A question on screen wins even if a spinner is also visible."""
        screen = CLAUDE_ASKING + "\n✻ thinking · esc to interrupt"
        assert detect.detect_state(screen, "claude") == "input"

    def test_codex_statement_is_not_a_question(self) -> None:
        assert detect.detect_state(CODEX_IDLE, "codex") == "idle"


class TestExtract:
    def test_name_from_argv_beats_everything(self) -> None:
        name = detect.extract_name(
            "claude --resume my-session", "✳ other-name", CLAUDE_IDLE
        )
        assert name == "my-session"

    def test_name_from_pane_title(self) -> None:
        assert detect.extract_name("", "✳ my-title", "") == "my-title"

    def test_name_strips_spinner_glyphs(self) -> None:
        assert detect.extract_name("", "⠹ farchat", "") == "farchat"

    def test_shell_titles_are_rejected(self) -> None:
        """Paths and hostnames are shell-set titles, not agent names."""
        assert detect.extract_name("", "~/Git/foo", "") == ""
        assert detect.extract_name("", "macbookpro.lan", "") == ""

    def test_name_from_separator_line(self) -> None:
        assert detect.extract_name("", "~[dir]", CLAUDE_IDLE) == "certify-main"

    def test_name_absent(self) -> None:
        assert detect.extract_name("", "", "nothing here") == ""

    def test_model_claude(self) -> None:
        assert detect.extract_model(CLAUDE_IDLE, "claude") == "fable"

    def test_model_codex(self) -> None:
        assert detect.extract_model(CODEX_IDLE, "codex") == "gpt-5.6-sol"


class TestAgentModel:
    def test_rank_orders_by_urgency(self) -> None:
        states = ["idle", "input", "bg", "busy"]
        agents = [Agent(pane=f"s:1.{i}", session="s", kind="claude", state=s)  # type: ignore[arg-type]
                  for i, s in enumerate(states)]
        agents.sort(key=lambda a: a.rank)
        assert [a.state for a in agents] == ["input", "busy", "bg", "idle"]

    def test_roundtrip_through_dict(self) -> None:
        agent = Agent(
            pane="sasy:1.4", session="sasy", kind="claude", state="bg", name="certify"
        )
        assert Agent.from_dict(agent.to_dict()) == agent

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = {"pane": "a:1.1", "session": "a", "kind": "codex", "bogus": 1}
        assert Agent.from_dict(data).pane == "a:1.1"


class TestRender:
    def _agents(self) -> list[Agent]:
        return [
            Agent(pane="sasy:1.4", session="sasy", kind="claude", state="input",
                  name="certify", repo="observability", branch="main"),
            Agent(pane="cc:1.1", session="cc", kind="codex", state="idle"),
        ]

    def test_row_contains_key_fields(self) -> None:
        row = render.picker_row(self._agents()[0], colour=False)
        assert "sasy:1.4" in row and "input" in row and "certify" in row
        assert "observability@main" in row

    def test_pane_is_first_field(self) -> None:
        """fzf addresses the pane as {1}; it must lead the row."""
        row = render.picker_row(self._agents()[0], colour=False)
        assert row.split()[0] == "sasy:1.4"

    def test_colour_can_be_disabled(self) -> None:
        assert "\033[" not in render.picker_row(self._agents()[0], colour=False)

    def test_colour_present_when_enabled(self) -> None:
        assert "\033[" in render.picker_row(self._agents()[0], colour=True)

    def test_table_reports_counts(self) -> None:
        out = render.table(self._agents(), colour=False)
        assert "2 agents" in out and "input=1" in out

    def test_table_handles_empty(self) -> None:
        assert "no agents" in render.table([], colour=False)

    def test_json_roundtrips(self) -> None:
        data = json.loads(render.as_json(self._agents()))
        assert [d["pane"] for d in data] == ["sasy:1.4", "cc:1.1"]


class TestCache:
    def test_write_then_read(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AMUX_CACHE", str(tmp_path / "amux.json"))
        agents = [Agent(pane="a:1.1", session="a", kind="claude", name="x")]
        cache.write(agents)
        loaded, age = cache.read()
        assert [a.pane for a in loaded] == ["a:1.1"]
        assert 0 <= age < 5

    def test_missing_cache_is_not_an_error(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AMUX_CACHE", str(tmp_path / "absent.json"))
        agents, age = cache.read()
        assert agents == [] and age == -1

    def test_stale_cache_is_rejected(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AMUX_CACHE", str(tmp_path / "amux.json"))
        cache.write([Agent(pane="a:1.1", session="a", kind="claude")])
        assert cache.read(max_age=-1)[0] == []

    def test_corrupt_cache_is_not_an_error(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "amux.json"
        path.write_text("{not json")
        monkeypatch.setenv("AMUX_CACHE", str(path))
        assert cache.read()[0] == []
