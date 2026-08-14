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

    def test_pane_is_the_tab_key_field(self) -> None:
        """fzf addresses the pane as {1} with --delimiter '\\t'."""
        line = render.picker_lines(self._agents()[:1], colour=False)
        assert line.split("\t", 1)[0] == "sasy:1.4"

    def test_session_name_with_space_survives(self) -> None:
        """tmux allows spaces in session names.

        Regression: with whitespace-delimited fields, fzf's {1} resolved to
        'amux' for a pane in session 'amux test', pointing the preview and the
        jump at a nonexistent pane.
        """
        agent = Agent(pane="amux test:1.1", session="amux test", kind="claude")
        line = render.picker_lines([agent], colour=False)
        assert render.pane_from_selection(line) == "amux test:1.1"

    def test_pane_recovered_from_selection_with_trailing_newline(self) -> None:
        agent = self._agents()[0]
        line = render.picker_lines([agent], colour=False) + "\n"
        assert render.pane_from_selection(line) == "sasy:1.4"

    def test_visible_row_still_shows_pane(self) -> None:
        """The key field is hidden via --with-nth, so the row repeats it."""
        row = render.picker_row(self._agents()[0], colour=False)
        assert row.startswith("sasy:1.4")

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


class TestPromptIsAtTheBottom:
    """Regression: a pending question lives at the bottom of the screen."""

    def test_old_question_text_in_history_is_not_input(self) -> None:
        screen = (
            'I changed the "Would you like" copy in the onboarding flow.\n'
            + "\n".join(f"  line {i}" for i in range(20))
            + "\n❯\n  ⏵⏵ bypass permissions on\n"
        )
        assert detect.detect_state(screen, "claude") == "idle"

    def test_current_question_is_input(self) -> None:
        screen = "some earlier output\n" * 20 + "Do you want to proceed?\n❯ 1. Yes\n"
        assert detect.detect_state(screen, "claude") == "input"


class TestCacheRobustness:
    def _set(self, tmp_path, monkeypatch, text: str):
        path = tmp_path / "amux.json"
        path.write_text(text)
        monkeypatch.setenv("AMUX_CACHE", str(path))
        return path

    def test_non_numeric_timestamp(self, tmp_path, monkeypatch) -> None:
        self._set(tmp_path, monkeypatch, '{"time":"not-a-number","agents":[]}')
        assert cache.read()[0] == []

    def test_null_entry_in_agents(self, tmp_path, monkeypatch) -> None:
        self._set(tmp_path, monkeypatch, '{"time":0,"agents":[null]}')
        assert cache.read()[0] == []

    def test_agents_not_a_list(self, tmp_path, monkeypatch) -> None:
        self._set(tmp_path, monkeypatch, '{"time":0,"agents":"nope"}')
        assert cache.read()[0] == []

    def test_fresh_cache_within_max_age(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AMUX_CACHE", str(tmp_path / "amux.json"))
        cache.write([Agent(pane="a:1.1", session="a", kind="claude")])
        assert len(cache.read(max_age=300)[0]) == 1

    def test_cache_older_than_max_age_is_rejected(self, tmp_path, monkeypatch) -> None:
        """A genuinely stale cache (not the trivial max_age=-1 case)."""
        import json as _json
        import time as _time

        path = tmp_path / "amux.json"
        stale = {"time": _time.time() - 600, "agents": [
            {"pane": "a:1.1", "session": "a", "kind": "claude"}]}
        path.write_text(_json.dumps(stale))
        monkeypatch.setenv("AMUX_CACHE", str(path))
        assert cache.read(max_age=60)[0] == []
        assert len(cache.read(max_age=3600)[0]) == 1


class TestScanWithStubbedTmux:
    """scan.py is testable without a live server by stubbing its shell calls."""

    SEP = "\x1f"

    def _listing(self, rows: list[tuple[str, str, str, str, str]]) -> str:
        return "\n".join(self.SEP.join(r) for r in rows)

    def test_only_agent_panes_are_returned(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        rows = [
            ("s:1.1", "s", "100", "✳ alpha", "/tmp"),
            ("s:1.2", "s", "200", "~/dir", "/tmp"),
        ]
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            self._listing(rows) if a[0] == "list-panes" else "agent screen text"))
        monkeypatch.setattr(scan_mod, "_child_argv_by_ppid",
                            lambda: {100: "claude --resume alpha", 200: "vim x"})
        monkeypatch.setattr(scan_mod, "_child_pid", lambda ppid, kind: ppid + 1)
        agents = scan_mod.scan(workers=2)
        assert [a.pane for a in agents] == ["s:1.1"]
        assert agents[0].name == "alpha" and agents[0].pid == 101

    def test_pane_that_dies_mid_scan_is_dropped(self, monkeypatch) -> None:
        """capture-pane returns '' for a pane that closed after listing."""
        from claude_code_tools.amux import scan as scan_mod

        rows = [("s:1.1", "s", "100", "t", "/tmp")]
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            self._listing(rows) if a[0] == "list-panes" else ""))
        monkeypatch.setattr(scan_mod, "_child_argv_by_ppid",
                            lambda: {100: "codex --yolo"})
        monkeypatch.setattr(scan_mod, "_child_pid", lambda ppid, kind: 0)
        assert scan_mod.scan(workers=1) == []

    def test_no_panes_at_all(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: "")
        assert scan_mod.scan() == []


class TestGitContext:
    def test_plain_repo(self, tmp_path) -> None:
        from claude_code_tools.amux import scan as scan_mod

        (tmp_path / "myrepo" / ".git").mkdir(parents=True)
        (tmp_path / "myrepo" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        repo, branch = scan_mod._git_context(str(tmp_path / "myrepo"))
        assert (repo, branch) == ("myrepo", "main")

    def test_relative_worktree_pointer(self, tmp_path) -> None:
        """Regression: relative gitdir: pointers resolved against amux's cwd."""
        from claude_code_tools.amux import scan as scan_mod

        real = tmp_path / "repo.git" / "worktrees" / "feature"
        real.mkdir(parents=True)
        (real / "HEAD").write_text("ref: refs/heads/feat/x\n")
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: ../repo.git/worktrees/feature\n")
        repo, branch = scan_mod._git_context(str(wt))
        assert (repo, branch) == ("wt", "feat/x")

    def test_outside_any_repo(self, tmp_path) -> None:
        from claude_code_tools.amux import scan as scan_mod

        assert scan_mod._git_context(str(tmp_path)) == ("", "")

    def test_empty_cwd(self) -> None:
        from claude_code_tools.amux import scan as scan_mod

        assert scan_mod._git_context("") == ("", "")


class TestCli:
    def test_default_command_keeps_global_options(self) -> None:
        """Regression: `amux --max-age 0` used to fall back to the 30s default."""
        from claude_code_tools.amux import cli

        parser = cli.build_parser()
        raw = ["--max-age", "0"]
        if not any(t in {"pick", "list", "scan", "rows"} for t in raw):
            raw.append("pick")
        args = parser.parse_args(raw)
        assert args.max_age == 0.0 and args.func is cli.cmd_pick

    def test_explicit_subcommand_still_parses(self) -> None:
        from claude_code_tools.amux import cli

        args = cli.build_parser().parse_args(["list", "--json"])
        assert args.json is True and args.func is cli.cmd_list

    def test_interpreter_path_is_shell_quoted(self) -> None:
        """fzf binds run through a shell; a spaced venv path must survive."""
        import shlex

        quoted = shlex.quote("/tmp/my venv/bin/python")
        assert quoted != "/tmp/my venv/bin/python"
        assert shlex.split(f"{quoted} -m x")[0] == "/tmp/my venv/bin/python"
