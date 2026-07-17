"""CLI-level tests for the claude -> codex direction of `aichat port`.

Split out of test_port_claude_to_codex.py to keep each test file
under the repo's 1000-line limit. Shares the synthetic-session
builder with that module. The mandatory E2E resume-verification
evidence lives in the comment block at the bottom of this file.
"""

import json
import uuid as uuid_mod
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from claude_code_tools.aichat import main
from tests.test_port_claude_to_codex import (
    CLAUDE_SID,
    ROLLOUT_NAME_RE,
    _ts,
    write_claude_session,
)


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


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCLI:
    """CLI-level contract for the claude -> codex direction."""

    def _invoke(
        self,
        runner: CliRunner,
        args: list[str],
        claude_home: Path,
        codex_home: Path,
    ) -> Result:
        return runner.invoke(
            main,
            [
                *args,
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
            ],
        )

    def test_claude_source_converts_and_prints_hints(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        write_claude_session(claude_home, project_dir)
        result = self._invoke(
            runner, ["port", CLAUDE_SID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: claude — porting to Codex"
            in result.output
        )
        assert "New Codex session id:" in result.output
        assert "Output file:" in result.output
        assert f"Session cwd:           {project_dir}" in result.output
        assert (
            f"cd {project_dir} && codex resume " in result.output
        )
        # one-line /import tip still present as the interactive
        # alternative
        assert "/import" in result.output
        # the rollout actually exists under the tmp codex home
        rollouts = list(
            (codex_home / "sessions").rglob("rollout-*.jsonl")
        )
        assert len(rollouts) == 1
        # printed id matches the created file
        m = ROLLOUT_NAME_RE.match(rollouts[0].name)
        assert m and m.group(1) in result.output

    def test_claude_source_direction_line_first(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        write_claude_session(claude_home, project_dir)
        result = self._invoke(
            runner, ["port", CLAUDE_SID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[0] == (
            "Detected source agent: claude — porting to Codex"
        )

    def test_claude_source_conversion_error_is_clean(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
    ) -> None:
        """A claude session with no portable messages errors cleanly."""
        proj = claude_home / "projects" / "-tmp-none"
        proj.mkdir(parents=True)
        sid = str(uuid_mod.uuid4())
        # valid claude session (detectable) whose only line is meta
        line = {
            "type": "user",
            "sessionId": sid,
            "cwd": "/tmp/x",
            "message": {
                "role": "user",
                "content": "<command-name>/clear</command-name>",
            },
            "uuid": str(uuid_mod.uuid4()),
            "timestamp": _ts(0),
        }
        (proj / f"{sid}.jsonl").write_text(
            json.dumps(line) + "\n", encoding="utf-8"
        )
        result = self._invoke(
            runner, ["port", sid], claude_home, codex_home
        )
        assert result.exit_code != 0
        try:
            stderr = result.stderr
        except ValueError:
            stderr = ""
        combined = result.output + stderr
        assert "No portable messages" in combined
        assert "Traceback" not in combined

    def test_codex_source_path_unchanged(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
    ) -> None:
        """Regression: the codex -> claude direction is untouched."""
        from tests.test_port_session import write_modern_rollout
        from tests.test_port_session import MODERN_UUID

        write_modern_rollout(codex_home, project_dir)
        result = self._invoke(
            runner, ["port", MODERN_UUID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )
        assert "New Claude session id:" in result.output
        assert f"cd {project_dir} && claude --resume " in result.output
        assert "/import" not in result.output

    def test_unknown_session_unchanged(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
    ) -> None:
        unknown_id = "deadbeef-0000-0000-0000-000000000000"
        result = self._invoke(
            runner, ["port", unknown_id], claude_home, codex_home
        )
        assert result.exit_code != 0
        try:
            stderr = result.stderr
        except ValueError:
            stderr = ""
        combined = result.output + stderr
        assert "Session not found in Claude or Codex" in combined
        assert unknown_id in combined


class TestCodexHomeEnvVar:
    """CODEX_HOME is honored, as the CLI help promises.

    Resolution precedence: --codex-home, then CODEX_HOME, then
    ~/.codex.
    """

    def test_converter_defaults_to_codex_home_env(
        self,
        claude_home: Path,
        project_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_home = tmp_path / "env-codex-home"
        env_home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(env_home))
        session = write_claude_session(claude_home, project_dir)
        from claude_code_tools.port_claude_to_codex import (
            port_claude_session_to_codex,
        )

        _, out_path = port_claude_session_to_codex(session)
        assert out_path.is_file()
        out_path.relative_to(env_home / "sessions")
        assert (env_home / "history.jsonl").is_file()

    def test_cli_port_honors_codex_home_env(
        self,
        runner: CliRunner,
        claude_home: Path,
        project_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_home = tmp_path / "env-codex-home"
        env_home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(env_home))
        write_claude_session(claude_home, project_dir)
        result = runner.invoke(
            main,
            [
                "port",
                CLAUDE_SID,
                "--claude-home",
                str(claude_home),
            ],
        )
        assert result.exit_code == 0, result.output
        rollouts = list(
            (env_home / "sessions").rglob("rollout-*.jsonl")
        )
        assert len(rollouts) == 1

    def test_cli_flag_overrides_codex_home_env(
        self,
        runner: CliRunner,
        claude_home: Path,
        codex_home: Path,
        project_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_home = tmp_path / "env-codex-home"
        env_home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(env_home))
        write_claude_session(claude_home, project_dir)
        result = runner.invoke(
            main,
            [
                "port",
                CLAUDE_SID,
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
            ],
        )
        assert result.exit_code == 0, result.output
        assert list(
            (codex_home / "sessions").rglob("rollout-*.jsonl")
        )
        assert not list(env_home.rglob("rollout-*.jsonl"))


# ---------------------------------------------------------------------
# E2E EVIDENCE (mandatory real-home resume verification, iteration 3,
# run 2026-07-17 against the FINAL working tree -- i.e. after the
# user turn-id passthrough, provenance-based reminder handling,
# CODEX_HOME resolution and history-tail fixes. Commands and output
# are captured COMPLETELY: nothing is elided; the only edits are
# that lines longer than 88 columns are wrapped with a trailing
# backslash. This supersedes the evidence recorded for iterations 1
# and 2, whose rollout shape differed (no user turn-id passthrough).
#
# Resume syntax was verified first via
# `codex exec resume --help` / `codex exec --help` (no -s/--color
# flags exist; sandbox is set via -c 'sandbox_mode=...').
#
# Step 0 -- pre-port state of the real Codex home:
#   $ wc -l /Users/pchalasani/.codex/history.jsonl
#   5376 /Users/pchalasani/.codex/history.jsonl
#
# Step 1 -- port a small (50KB) real Claude session into the REAL
# ~/.codex using the final working-tree converter:
#   $ .venv/bin/python -c "
#   from claude_code_tools.port_claude_to_codex import \
#       port_claude_session_to_codex
#   nid, path = port_claude_session_to_codex(\
#   '/Users/pchalasani/.claude/projects/\
#   -Users-pchalasani-Git-claude-code-tools/\
#   c0f3527b-1964-48c3-a181-adafea652579.jsonl')
#   print(nid); print(path)"
#   019f700a-10c0-7c40-96dd-5c71bd4ffe21
#   /Users/pchalasani/.codex/sessions/2026/07/17/\
#   rollout-2026-07-17T08-25-35-\
#   019f700a-10c0-7c40-96dd-5c71bd4ffe21.jsonl
#
# Step 2 -- headless resume from the session's cwd, stdin closed,
# 240s timeout (complete output follows; exit status printed last):
#   $ cd /Users/pchalasani/Git/claude-code-tools && timeout 240 \
#     /Users/pchalasani/.nvm/versions/node/v22.23.0/bin/codex \
#     exec resume 019f700a-10c0-7c40-96dd-5c71bd4ffe21 \
#     --skip-git-repo-check -c 'sandbox_mode="read-only"' \
#     "Reply with exactly OK" < /dev/null 2>&1; echo "EXIT_CODE=$?"
#   2026-07-17T12:25:41.557791Z ERROR codex_core::session::session: \
#   failed to load skill /Users/pchalasani/.local/share/agent-skills/\
#   agent-style/upstream/skills/style-review/SKILL.md: missing YAML \
#   frontmatter delimited by ---
#   2026-07-17T12:25:41.557869Z ERROR codex_core::session::session: \
#   failed to load skill /Users/pchalasani/.agents/skills/\
#   tdd-implement/SKILL.md: invalid YAML: could not find expected \
#   ':' at line 4 column 1, while scanning a simple key at line 3 \
#   column 1
#   OpenAI Codex v0.144.5
#   --------
#   workdir: /Users/pchalasani/Git/claude-code-tools
#   model: gpt-5.6-sol
#   provider: openai
#   approval: never
#   sandbox: read-only
#   reasoning effort: high
#   reasoning summaries: detailed
#   session id: 019f700a-10c0-7c40-96dd-5c71bd4ffe21
#   --------
#   user
#   Reply with exactly OK
#   2026-07-17T12:25:41.647530Z ERROR rmcp::transport::worker: \
#   worker quit with fatal: Transport channel closed, when \
#   AuthRequired(AuthRequiredError { www_authenticate_header: \
#   "Bearer error=\"invalid_request\", error_description=\"No \
#   access token was provided in this request\", \
#   resource_metadata=\"https://api.githubcopilot.com/\
#   .well-known/oauth-protected-resource/mcp/\"" })
#   warning: Skill descriptions were shortened to fit the 2% skills \
#   context budget. Codex can still see every skill, but some \
#   descriptions are shorter. Disable unused skills or plugins to \
#   leave more room for the rest.
#   codex
#   OK
#   tokens used
#   8,857
#   OK
#   EXIT_CODE=0
#
# (The two "failed to load skill" lines and the MCP AuthRequired
# line are pre-existing local-environment noise, unrelated to the
# ported rollout: no schema/parse error was reported, the printed
# session id matches the ported id, codex loaded the synthesized
# rollout and replied "OK", and the command exited 0.)
#
# Step 3 -- cleanup: rollout deleted; the single history.jsonl line
# appended by the port removed; pre-port line count restored:
#   $ rm /Users/pchalasani/.codex/sessions/2026/07/17/\
#   rollout-2026-07-17T08-25-35-\
#   019f700a-10c0-7c40-96dd-5c71bd4ffe21.jsonl
#   $ grep -c "019f700a-10c0-7c40-96dd-5c71bd4ffe21" \
#     /Users/pchalasani/.codex/history.jsonl
#   1
#   $ grep -v "019f700a-10c0-7c40-96dd-5c71bd4ffe21" \
#     /Users/pchalasani/.codex/history.jsonl \
#     > /Users/pchalasani/.codex/history.jsonl.tmp && \
#     mv /Users/pchalasani/.codex/history.jsonl.tmp \
#     /Users/pchalasani/.codex/history.jsonl
#   $ wc -l /Users/pchalasani/.codex/history.jsonl
#   5376 /Users/pchalasani/.codex/history.jsonl
#
# ITERATION-6 RE-RUN (2026-07-17, against the FINAL working tree --
# after the strict matching-tag agent-notification validation, the
# complete-shape [SESSION LINEAGE] validation, and the shared
# CLAUDE_INTERNAL_WRAPPER_TAGS move into export_session; the
# session_meta payload carries BOTH `session_id` and `id`, equal,
# root-session shape). Commands and output are captured COMPLETELY:
# nothing is elided; the only edits are that lines longer than 88
# columns are wrapped with a trailing backslash. This supersedes the
# earlier iteration-5 evidence, which recorded only an output tail.
#
# Step 0 -- pre-port state of the real Codex home:
#   $ wc -l /Users/pchalasani/.codex/history.jsonl
#   5376 /Users/pchalasani/.codex/history.jsonl
#
# Step 1 -- port a small (50KB) real Claude session into the REAL
# ~/.codex using the final working-tree converter:
#   $ .venv/bin/python -c "
#   from claude_code_tools.port_claude_to_codex import \
#       port_claude_session_to_codex
#   nid, path = port_claude_session_to_codex(\
#   '/Users/pchalasani/.claude/projects/\
#   -Users-pchalasani-Git-claude-code-tools/\
#   540fbf82-60e8-4018-8cdd-6f5f97972c18.jsonl')
#   print(nid); print(path)"
#   019f7029-6cf4-72ea-817f-778705599669
#   /Users/pchalasani/.codex/sessions/2026/07/17/\
#   rollout-2026-07-17T08-59-50-\
#   019f7029-6cf4-72ea-817f-778705599669.jsonl
#
# Step 2 -- headless resume from the session's cwd
# (/Users/pchalasani/Git/claude-code-tools), stdin closed, 240s
# timeout (COMPLETE output follows; exit status printed last):
#   $ cd /Users/pchalasani/Git/claude-code-tools && timeout 240 \
#     /Users/pchalasani/.nvm/versions/node/v22.23.0/bin/codex \
#     exec --skip-git-repo-check -s read-only --color never \
#     resume 019f7029-6cf4-72ea-817f-778705599669 \
#     "Reply with exactly OK" < /dev/null 2>&1; echo "EXIT_CODE=$?"
#   2026-07-17T12:59:56.273152Z ERROR codex_core::session::session: \
#   failed to load skill /Users/pchalasani/.local/share/agent-skills/\
#   agent-style/upstream/skills/style-review/SKILL.md: missing YAML \
#   frontmatter delimited by ---
#   2026-07-17T12:59:56.273258Z ERROR codex_core::session::session: \
#   failed to load skill /Users/pchalasani/.agents/skills/\
#   tdd-implement/SKILL.md: invalid YAML: could not find expected \
#   ':' at line 4 column 1, while scanning a simple key at line 3 \
#   column 1
#   OpenAI Codex v0.144.5
#   --------
#   workdir: /Users/pchalasani/Git/claude-code-tools
#   model: gpt-5.6-sol
#   provider: openai
#   approval: never
#   sandbox: read-only
#   reasoning effort: high
#   reasoning summaries: detailed
#   session id: 019f7029-6cf4-72ea-817f-778705599669
#   --------
#   user
#   Reply with exactly OK
#   2026-07-17T12:59:56.393478Z ERROR rmcp::transport::worker: \
#   worker quit with fatal: Transport channel closed, when \
#   AuthRequired(AuthRequiredError { www_authenticate_header: \
#   "Bearer error=\"invalid_request\", error_description=\"No \
#   access token was provided in this request\", \
#   resource_metadata=\"https://api.githubcopilot.com/\
#   .well-known/oauth-protected-resource/mcp/\"" })
#   warning: Skill descriptions were shortened to fit the 2% skills \
#   context budget. Codex can still see every skill, but some \
#   descriptions are shorter. Disable unused skills or plugins to \
#   leave more room for the rest.
#   codex
#   OK
#   tokens used
#   11,997
#   OK
#   EXIT_CODE=0
#
# (The two "failed to load skill" lines and the MCP AuthRequired
# line are pre-existing local-environment noise, unrelated to the
# ported rollout: no schema/parse error was reported, the printed
# session id matches the ported id, codex loaded the synthesized
# rollout and replied "OK", and the command exited 0.)
#
# Step 3 -- cleanup: rollout deleted; the single history.jsonl line
# appended by the port removed; pre-port line count restored:
#   $ rm /Users/pchalasani/.codex/sessions/2026/07/17/\
#   rollout-2026-07-17T08-59-50-\
#   019f7029-6cf4-72ea-817f-778705599669.jsonl && \
#     grep -c "019f7029-6cf4-72ea-817f-778705599669" \
#     /Users/pchalasani/.codex/history.jsonl && \
#     grep -v "019f7029-6cf4-72ea-817f-778705599669" \
#     /Users/pchalasani/.codex/history.jsonl \
#     > /Users/pchalasani/.codex/history.jsonl.tmp && \
#     mv /Users/pchalasani/.codex/history.jsonl.tmp \
#     /Users/pchalasani/.codex/history.jsonl && \
#     wc -l /Users/pchalasani/.codex/history.jsonl && \
#     ls /Users/pchalasani/.codex/sessions/2026/07/17/ | \
#     grep 019f7029 | wc -l
#   1
#       5376 /Users/pchalasani/.codex/history.jsonl
#          0
