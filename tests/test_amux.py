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

    def test_spinner_below_a_prompt_means_it_was_answered(self) -> None:
        """Position decides, not mere presence.

        A spinner rendered BELOW the choices means the user already answered
        and work resumed -- that pane is busy, not blocking.
        """
        screen = CLAUDE_ASKING + "\n✻ thinking · esc to interrupt"
        assert detect.detect_state(screen, "claude") == "busy"

    def test_prompt_below_a_spinner_is_still_blocking(self) -> None:
        """Claude worked, then stopped to ask: the prompt is last, so input."""
        screen = "✻ Cooked for 2m · esc to interrupt\n" + CLAUDE_ASKING
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

    REC = "\x1e"

    def _listing(self, rows: list[tuple[str, str, str, str, str]]) -> str:
        """Mimic REAL tmux -F output.

        tmux terminates each -F line with a newline AFTER our record marker,
        so the wire form is ``record + REC + "\n"``. Joining records
        REC-adjacent (no newline) meant the leading-newline strip in scan()
        was never exercised, and deleting it left these tests green.
        """
        return "".join(self.SEP.join(r) + self.REC + "\n" for r in rows)

    def test_only_agent_panes_are_returned(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        rows = [
            ("s:1.1", "s", "100", "✳ alpha", "/tmp"),
            ("s:1.2", "s", "200", "~/dir", "/tmp"),
        ]
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            self._listing(rows) if a[0] == "list-panes" else "agent screen text"))
        monkeypatch.setattr(scan_mod, "_children_by_ppid",
                            lambda: {100: [(101, "claude --resume alpha")],
                                     200: [(201, "vim x")]})
        agents = scan_mod.scan(workers=2)
        assert [a.pane for a in agents] == ["s:1.1"]
        assert agents[0].name == "alpha" and agents[0].pid == 101

    def test_pane_that_dies_mid_scan_is_dropped(self, monkeypatch) -> None:
        """capture-pane returns '' for a pane that closed after listing."""
        from claude_code_tools.amux import scan as scan_mod

        rows = [("s:1.1", "s", "100", "t", "/tmp")]
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            self._listing(rows) if a[0] == "list-panes" else ""))
        monkeypatch.setattr(scan_mod, "_children_by_ppid",
                            lambda: {100: [(101, "codex --yolo")]})
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
    def test_default_command_keeps_global_options(self, monkeypatch) -> None:
        """Regression: `amux --max-age 0` fell back to the 30s default.

        Drives main() so that reverting the argv rewrite fails this test.
        """
        from claude_code_tools.amux import cli

        seen: dict[str, object] = {}
        monkeypatch.setattr(cli.scan, "tmux_available", lambda: True)
        # build_parser() resolves cmd_pick when main() calls it, so patching
        # the module global here is enough to intercept the dispatch.
        monkeypatch.setattr(
            cli, "cmd_pick", lambda a: seen.update(max_age=a.max_age) or 0
        )
        cli.main(["--max-age", "0"])
        assert seen["max_age"] == 0.0

    def test_explicit_subcommand_still_parses(self) -> None:
        from claude_code_tools.amux import cli

        args = cli.build_parser().parse_args(["list", "--json"])
        assert args.json is True and args.func is cli.cmd_list

    def test_fzf_bind_quotes_a_spaced_interpreter_path(self, monkeypatch) -> None:
        """The reload bind is shell-executed; a spaced venv path must survive.

        Asserts on the actual argv handed to fzf, so removing shlex.quote()
        from cmd_pick() fails this test.
        """
        import shlex as _shlex

        from claude_code_tools.amux import cli
        from claude_code_tools.amux.model import Agent as _A

        captured: dict[str, list[str]] = {}

        class _Proc:
            returncode = 1
            stdout = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Proc()

        monkeypatch.setattr(cli.sys, "executable", "/tmp/my venv/bin/python")
        monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/fzf")
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(
            cli, "_agents_for_display",
            lambda _: ([_A(pane="a:1.1", session="a", kind="claude")], False),
        )
        monkeypatch.setattr(cli.subprocess, "run", fake_run)

        import argparse as _ap

        cli.cmd_pick(_ap.Namespace(max_age=30.0))
        binds = [c for c in captured["cmd"] if "reload(" in c]
        assert binds, "no reload bind was built"
        inner = binds[0].split("reload(", 1)[1].rstrip(")")
        assert _shlex.split(inner)[0] == "/tmp/my venv/bin/python"


class TestRecordParsing:
    """Pane records must survive newlines in titles/paths without mangling.

    Regression: an earlier fix used tmux's "#{s/\\n/ /:...}" substitution,
    whose pattern is a literal string -- it rewrote every letter 'n', turning
    /Users/pchalasani/Git/avon into "/Users/pchalasa i/Git/avo ". Paths were
    silently corrupted and git context came back blank for most panes.
    """

    def test_field_values_are_never_rewritten(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        cwd = "/Users/pchalasani/Git/avon"
        rec = scan_mod._SEP.join(["s:1.1", "s", "100", "title", cwd])
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            rec + scan_mod._REC + "\n" if a[0] == "list-panes" else "screen"))
        monkeypatch.setattr(scan_mod, "_children_by_ppid",
                            lambda: {100: [(101, "claude --resume x")]})
        agents = scan_mod.scan(workers=1)
        assert agents[0].cwd == cwd

    def test_newline_in_title_does_not_drop_the_pane(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        rec = scan_mod._SEP.join(["s:1.1", "s", "100", "two\nlines", "/tmp"])
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            rec + scan_mod._REC + "\n" if a[0] == "list-panes" else "screen"))
        monkeypatch.setattr(scan_mod, "_children_by_ppid",
                            lambda: {100: [(101, "codex --yolo")]})
        assert [a.pane for a in scan_mod.scan(workers=1)] == ["s:1.1"]

    def test_scan_requests_the_record_terminator(self, monkeypatch) -> None:
        """The format string itself must carry the marker.

        Regression: proving a synthetic REC-delimited string can be split says
        nothing about what scan() asks tmux for.
        """
        from claude_code_tools.amux import scan as scan_mod

        seen: dict[str, str] = {}

        def fake_tmux(*args):
            if args[0] == "list-panes":
                seen["fmt"] = args[-1]
            return ""

        monkeypatch.setattr(scan_mod, "_tmux", fake_tmux)
        scan_mod.scan()
        # Assert the literal, not scan_mod._REC -- comparing against the
        # module constant passes even if that constant is emptied.
        assert seen["fmt"].endswith("\x1e")
        assert "s/" not in seen["fmt"], "must not use tmux format substitution"

    def test_multiple_records_split_correctly(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        recs = "".join(
            scan_mod._SEP.join([f"s:1.{i}", "s", str(100 + i), "t", "/tmp"])
            + scan_mod._REC
            + "\n"
            for i in range(3)
        )
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            recs if a[0] == "list-panes" else "screen"))
        monkeypatch.setattr(scan_mod, "_children_by_ppid",
                            lambda: {100 + i: [(200 + i, "claude x")]
                                     for i in range(3)})
        # Assert the VALUES, not just the count: without the leading-newline
        # strip every record after the first yields pane "\ns:1.N", which still
        # counts as 3 but cannot be captured or selected.
        assert [a.pane for a in scan_mod.scan(workers=2)] == [
            "s:1.0", "s:1.1", "s:1.2"
        ]


class TestCacheTypeValidation:
    """A well-shaped cache record can still hold wrong-typed fields."""

    def test_null_pane_is_dropped_not_rendered(self, tmp_path, monkeypatch) -> None:
        """Regression: reached render._clip() and crashed on len(None)."""
        import time as _t

        path = tmp_path / "amux.json"
        # A CURRENT timestamp: a future one is rejected before agents are
        # parsed, so this test would not reach Agent.from_dict at all.
        path.write_text(
            '{"time":%f,"agents":['
            '{"pane":null,"session":"s","kind":"claude"}]}' % _t.time()
        )
        monkeypatch.setenv("AMUX_CACHE", str(path))
        agents, _ = cache.read()
        assert agents == []
        render.picker_lines(agents, colour=False)  # must not raise

    def test_wrong_typed_optional_field_is_dropped(self, tmp_path, monkeypatch) -> None:
        import time as _t

        path = tmp_path / "amux.json"
        path.write_text(
            '{"time":%f,"agents":['
            '{"pane":"a:1.1","session":"s","kind":"claude","name":123}]}' % _t.time()
        )
        monkeypatch.setenv("AMUX_CACHE", str(path))
        assert cache.read()[0] == []

    def test_valid_record_still_loads(self, tmp_path, monkeypatch) -> None:
        """Validation must not reject legitimate cache entries."""
        monkeypatch.setenv("AMUX_CACHE", str(tmp_path / "amux.json"))
        cache.write([Agent(pane="a:1.1", session="a", kind="claude", pid=7)])
        loaded, _ = cache.read()
        assert len(loaded) == 1 and loaded[0].pid == 7


class TestAnsweredPromptIsNotBlocking:
    """An answered prompt leaves its choices on screen; only footer position
    distinguishes it from one still waiting."""

    ANSWERED = """
  Which do you prefer?
❯ 1. Yes
  2. No
  Understood, rebasing now.
  ctx ████░░░░░░ 44%
  ⏵⏵ bypass permissions on
❯
"""
    PENDING = """
  Understood, here are the options.
  Which do you prefer?
❯ 1. Yes
  2. No
"""

    def test_answered_prompt_is_not_input(self) -> None:
        assert detect.detect_state(self.ANSWERED, "claude") == "idle"

    def test_pending_prompt_is_input(self) -> None:
        assert detect.detect_state(self.PENDING, "claude") == "input"


class TestCacheEnumAndClock:
    def _write(self, tmp_path, monkeypatch, body: str):
        path = tmp_path / "amux.json"
        path.write_text(body)
        monkeypatch.setenv("AMUX_CACHE", str(path))

    def test_unknown_kind_is_rejected(self, tmp_path, monkeypatch) -> None:
        self._write(tmp_path, monkeypatch,
                    '{"time":0,"agents":[{"pane":"s:1.1","session":"s",'
                    '"kind":"vim"}]}')
        assert cache.read(max_age=None)[0] == []

    def test_unknown_state_is_rejected(self, tmp_path, monkeypatch) -> None:
        self._write(tmp_path, monkeypatch,
                    '{"time":0,"agents":[{"pane":"s:1.1","session":"s",'
                    '"kind":"claude","state":"waiting"}]}')
        assert cache.read(max_age=None)[0] == []

    def test_known_kind_and_state_accepted(self, tmp_path, monkeypatch) -> None:
        self._write(tmp_path, monkeypatch,
                    '{"time":0,"agents":[{"pane":"s:1.1","session":"s",'
                    '"kind":"codex","state":"busy"}]}')
        assert len(cache.read(max_age=None)[0]) == 1

    def test_future_timestamp_never_counts_as_fresh(self, tmp_path, monkeypatch) -> None:
        """Regression: negative age passed every max_age check indefinitely."""
        self._write(tmp_path, monkeypatch,
                    '{"time":9999999999,"agents":[{"pane":"s:1.1",'
                    '"session":"s","kind":"claude"}]}')
        assert cache.read(max_age=30)[0] == []


class TestNaNTimestamp:
    def test_nan_timestamp_is_not_fresh(self, tmp_path, monkeypatch) -> None:
        """Regression: every comparison against NaN is False, so a NaN age
        passed both the negative check and max_age -- cached forever."""
        path = tmp_path / "amux.json"
        path.write_text(
            '{"time":NaN,"agents":[{"pane":"s:1.1","session":"s",'
            '"kind":"claude"}]}'
        )
        monkeypatch.setenv("AMUX_CACHE", str(path))
        assert cache.read(max_age=30)[0] == []


class TestOptionTextResemblingFooter:
    def test_option_quoting_footer_text_stays_blocking(self) -> None:
        """Regression: an option mentioning footer words made a PENDING
        prompt look answered, hiding a genuinely blocked agent."""
        screen = (
            "Which action should I take?\n"
            "❯ 1. Continue\n"
            "  2. Explain why the status says bypass permissions on\n"
        )
        assert detect.detect_state(screen, "claude") == "input"

    def test_option_mentioning_ctx_bar_stays_blocking(self) -> None:
        screen = (
            "Pick one?\n"
            "❯ 1. Yes\n"
            "  2. Show me the ctx ████ meter\n"
        )
        assert detect.detect_state(screen, "claude") == "input"


class TestFooterMatchesRealHarnessChrome:
    """Footer patterns are checked against lines captured from live panes.

    The synthetic fixtures elsewhere in this file only covered Claude, so the
    Codex status line was not recognised as footer chrome -- meaning a Codex
    pane whose message contained prompt-like text would stay flagged 'input'
    indefinitely. These strings are verbatim from real panes.
    """

    REAL_FOOTERS = [
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
        "  ctx ████████░░ 86%   5h ░░░░░░░░░░ 3% ↻4h29m",
        "  gpt-5.6-sol medium · farchat · main · Context 51% used",
        "  gpt-5.6-sol high · claude-code-tools.feat-brief-v2 · feat/brief-v2",
        "› Write tests for @filename",
        "                        new task? /clear to save 317.5k tokens",
    ]

    REAL_CONTENT = [
        "• Model changed to gpt-5.6-terra medium",
        "⚠ MCP client for `gpt-codex` failed to start: MCP startup failed",
        "  I changed the ctx meter copy in the onboarding flow.",
        "  2. Explain why the status says bypass permissions on",
    ]

    @pytest.mark.parametrize("line", REAL_FOOTERS)
    def test_real_footer_lines_are_recognised(self, line: str) -> None:
        assert detect._FOOTER.search(line), f"footer not matched: {line!r}"

    @pytest.mark.parametrize("line", REAL_CONTENT)
    def test_content_is_not_mistaken_for_footer(self, line: str) -> None:
        assert not detect._FOOTER.search(line), f"content matched: {line!r}"

    def test_codex_question_below_content_needs_footer_recognition(self) -> None:
        """Footer recognition, not the trailing-line accident, must decide.

        The earlier version of this test ended on a statement, so the Codex
        heuristic returned idle before footer matching mattered -- it passed
        with footer handling removed. Here the QUESTION is the last content
        line, so only recognising the Codex status line below it as chrome
        keeps this from being read as a pending prompt... and because that
        chrome IS below it, the pane is genuinely idle.
        """
        screen = (
            "• Ran the suite; all green.\n"
            "  Should I delete the stale config?\n"
            "  gpt-5.6-sol medium · farchat · main · Context 51% used\n"
            "›\n"
        )
        # The question is the last content line and nothing is working, so
        # this IS a pending prompt: the value of footer recognition here is
        # that the status line is not mistaken FOR content.
        assert detect.detect_state(screen, "codex") == "input"


class TestFooterAnchorSpecifically:
    """The anchor's job: footer chrome quoted INSIDE a content line is content.

    The earlier 'option quoting footer text' test did not actually exercise
    the anchor -- it relied on word alternatives the regex no longer uses, so
    un-anchoring left it green. These use the real chrome glyphs mid-line.
    """

    def test_chrome_glyph_midline_is_not_footer(self) -> None:
        line = "  I ran it and the status bar showed ⏵⏵ bypass permissions on"
        assert not detect._FOOTER.search(line)

    def test_ctx_bar_midline_is_not_footer(self) -> None:
        line = "  The output contained ctx ████ which confused the parser"
        assert not detect._FOOTER.search(line)

    def test_codex_model_midline_is_not_footer(self) -> None:
        line = "• Model changed to gpt-5.6-terra medium"
        assert not detect._FOOTER.search(line)

    def test_same_glyph_at_line_start_is_footer(self) -> None:
        assert detect._FOOTER.search("  ⏵⏵ bypass permissions on")

    def test_pending_prompt_quoting_chrome_stays_blocking(self) -> None:
        """End to end: an option quoting chrome must not look answered."""
        screen = (
            "Which action should I take?\n"
            "❯ 1. Continue\n"
            "  2. Explain the ⏵⏵ bypass permissions on indicator\n"
        )
        assert detect.detect_state(screen, "claude") == "input"


class TestMaxAgeValidation:
    def test_nan_is_rejected(self) -> None:
        """Regression: NaN max_age disabled expiry, serving stale data."""
        from claude_code_tools.amux import cli

        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--max-age", "nan", "list"])

    def test_inf_is_rejected(self) -> None:
        from claude_code_tools.amux import cli

        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--max-age", "inf", "list"])

    def test_negative_is_rejected(self) -> None:
        from claude_code_tools.amux import cli

        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--max-age", "-5", "list"])

    def test_ordinary_value_accepted(self) -> None:
        from claude_code_tools.amux import cli

        assert cli.build_parser().parse_args(["--max-age", "0", "list"]).max_age == 0.0


class TestCodexBusyBeatsStaleQuestion:
    """A visible spinner outranks an older question still on screen.

    Regression: Codex asks, you answer, it starts working -- the question is
    still rendered above, and the heuristic reported 'input' while the agent
    was plainly busy.
    """

    ANSWERED_AND_WORKING = """• Would you like me to run the full suite?
› Yes, please run it.
• Waiting for background terminal (1m 02s • esc to interrupt)
  gpt-5.6-sol high · repo · main"""

    STILL_ASKING = """• I found two candidate configs.
  Should I delete the stale one before continuing?
›
  gpt-5.6-sol high · farchat · main"""

    def test_working_after_an_answer_is_busy(self) -> None:
        assert detect.detect_state(self.ANSWERED_AND_WORKING, "codex") == "busy"

    def test_genuine_pending_question_is_still_input(self) -> None:
        assert detect.detect_state(self.STILL_ASKING, "codex") == "input"

    def test_question_far_up_the_scrollback_is_ignored(self) -> None:
        """Only the tail counts; an old question must not pin the state."""
        screen = (
            "• Should I continue?\n"
            + "\n".join(f"• step {i} done." for i in range(20))
            + "\n›\n  gpt-5.6-sol high · repo · main"
        )
        assert detect.detect_state(screen, "codex") == "idle"


class TestExecutableOnlyMatching:
    """Only the executable identifies a harness, never its arguments.

    Regression (PR review): the patterns searched the whole command line, so
    `rg claude .` in a pane was listed as an idle Claude agent.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "rg claude .",
            "grep -r codex /src",
            "vim claude_notes.md",
            "less /var/log/codex.log",
            "git commit -m 'fix codex handling'",
        ],
    )
    def test_agent_name_in_arguments_is_not_an_agent(self, cmd: str) -> None:
        assert detect.classify_argv(cmd) is None

    @pytest.mark.parametrize(
        "cmd,kind",
        [
            ("claude --resume x", "claude"),
            ("/usr/local/bin/codex --yolo", "codex"),
            ("node /p/node_modules/@openai/codex/bin/codex.js --yolo", "codex"),
            ("/Users/p/.local/share/claude/versions/2.1.220 --resume y", "claude"),
        ],
    )
    def test_real_launchers_still_match(self, cmd: str, kind: str) -> None:
        assert detect.classify_argv(cmd) == kind


class TestDescendantTraversal:
    """An agent under a wrapper process must still be found.

    Regression (PR review): only direct children were inspected, so a pane
    shell that launches a wrapper which launches the agent showed only the
    wrapper and the agent was invisible.
    """

    TREE = {
        100: [(101, "direnv exec . zsh")],
        101: [(102, "claude --resume nested")],
    }

    def test_finds_agent_two_levels_down(self) -> None:
        from claude_code_tools.amux import scan as scan_mod

        found = scan_mod.descendants(self.TREE, 100)
        assert detect.classify_argv(scan_mod.argv_of(found)) == "claude"
        assert scan_mod.agent_pid(found, "claude") == 102

    def test_direct_child_still_works(self) -> None:
        from claude_code_tools.amux import scan as scan_mod

        tree = {100: [(101, "codex --yolo")]}
        found = scan_mod.descendants(tree, 100)
        assert scan_mod.agent_pid(found, "codex") == 101

    def test_cycle_does_not_hang(self) -> None:
        from claude_code_tools.amux import scan as scan_mod

        cyclic = {100: [(101, "a")], 101: [(100, "b")]}
        assert len(scan_mod.descendants(cyclic, 100)) <= 2

    def test_scan_finds_a_nested_agent(self, monkeypatch) -> None:
        from claude_code_tools.amux import scan as scan_mod

        rec = scan_mod._SEP.join(["s:1.1", "s", "100", "t", "/tmp"])
        monkeypatch.setattr(scan_mod, "_tmux", lambda *a: (
            rec + scan_mod._REC + "\n" if a[0] == "list-panes" else "screen"))
        monkeypatch.setattr(scan_mod, "_children_by_ppid", lambda: self.TREE)
        agents = scan_mod.scan(workers=1)
        assert [(a.pane, a.kind, a.pid) for a in agents] == [("s:1.1", "claude", 102)]
        assert agents[0].name == "nested"


class TestFzfStderrReachesTheTerminal:
    """fzf draws its interface on stderr; capturing it hangs the picker.

    Regression: subprocess.run(..., capture_output=True) piped stderr as well
    as stdout, so the UI never reached the terminal and amux appeared to hang
    waiting for keys against an invisible prompt. Only stdout may be captured.
    """

    def test_only_stdout_is_captured(self, monkeypatch) -> None:
        import argparse as _ap
        import subprocess as _sp

        from claude_code_tools.amux import cli
        from claude_code_tools.amux.model import Agent as _A

        seen: dict[str, object] = {}

        class _Proc:
            returncode = 1
            stdout = ""

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return _Proc()

        monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/fzf")
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(
            cli, "_agents_for_display",
            lambda _: ([_A(pane="a:1.1", session="a", kind="claude")], False),
        )
        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli.cmd_pick(_ap.Namespace(max_age=30.0))

        assert seen.get("capture_output") is not True, "must not capture stderr"
        assert seen.get("stderr") is None, "stderr must be inherited"
        assert seen.get("stdout") is _sp.PIPE, "stdout must be captured"


class TestWorkingWithoutEscToInterrupt:
    """Claude signals work in more than one way; only one mentions 'esc'.

    Regression: sasy:1.4 had a subagent actively running and reported 'idle'.
    All strings here are verbatim from live panes.
    """

    MAIN_SPINNER = "· Razzmatazzing… (1m 42s · ↓ 4.8k tokens · thought for 14s)"
    SUBAGENT_ROW = (
        "  ◯ cache-incremental-evidence  Grepping running.mdx for stale counts"
        "                  1m 16s · ↓ 873.5k tokens"
    )
    FOOTER = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent"

    def test_main_spinner_without_esc_is_busy(self) -> None:
        assert detect.detect_state(f"{self.MAIN_SPINNER}\n{self.FOOTER}", "claude") == "busy"

    def test_running_subagent_is_background_work(self) -> None:
        screen = f"{self.FOOTER}\n  ⏺ main\n{self.SUBAGENT_ROW}"
        assert detect.detect_state(screen, "claude") == "bg"

    def test_agent_marker_alone_is_not_background(self) -> None:
        """'← 1 agent' appears on idle panes too; it must not imply work."""
        assert detect.detect_state(self.FOOTER, "claude") == "idle"

    def test_finished_output_line_is_not_background(self) -> None:
        """⏺ prefixes ordinary assistant output, not just tree rows."""
        screen = "⏺ The keepalive is built and done.\n" + self.FOOTER
        assert detect.detect_state(screen, "claude") == "idle"

    def test_finished_turn_with_live_monitor_is_background(self) -> None:
        """'Baked for 16m 24s' is past tense -- the turn ended, but a monitor
        is still going, which is precisely what bg means."""
        screen = "✻ Baked for 16m 24s · 1 monitor still running"
        assert detect.detect_state(screen, "claude") == "bg"
