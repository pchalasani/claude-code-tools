"""Tests for `aichat port --provider`: moving a Codex session between providers.

The fixtures are rollout-shaped like real Codex files: a session_meta first
line (plus a lineage session_meta for a forked thread), turn_context,
user/agent event messages, response items with encrypted reasoning, a
thread_settings_applied event naming the provider, and a compacted record
whose replacement and guardian histories hold encrypted items too.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from claude_code_tools.aichat import main
from claude_code_tools.port_codex_provider import (
    retarget_codex_session,
    validate_provider,
)
from claude_code_tools.port_render import PortDisplay
from claude_code_tools.port_service import (
    PortResult,
    PortSessionError,
    port_claude_session,
    retarget_codex_session as retarget_service,
)
from tests.test_port_claude_to_codex import write_claude_session

SID = "01a1a2a9-9074-7af3-b0fc-5e7259153266"
PARENT = "01a19db7-6c22-7593-b2ac-e8fa6e44f968"
TS = "2026-11-03T18:26:24.000Z"


def _line(kind: str, payload: dict, ts: str = TS) -> dict:
    return {"timestamp": ts, "type": kind, "payload": payload}


def rollout_records(cwd: str, provider: str = "openai") -> list[dict]:
    """A small but complete Codex rollout, as Codex writes it."""
    reasoning = {"type": "reasoning", "summary": [],
                 "encrypted_content": "gAAAAB-opaque"}
    return [
        _line("session_meta", {
            "session_id": SID, "id": SID, "forked_from_id": PARENT,
            "timestamp": TS, "cwd": cwd, "originator": "codex_exec",
            "cli_version": "0.166.0", "source": "exec",
            "thread_source": "user", "model_provider": provider}),
        _line("session_meta", {
            "session_id": PARENT, "id": PARENT, "timestamp": TS, "cwd": cwd,
            "originator": "codex_exec", "cli_version": "0.166.0",
            "model_provider": provider}),
        _line("turn_context", {"cwd": cwd, "model": "gpt-6-astra"}),
        _line("event_msg", {"type": "thread_settings_applied",
                            "thread_settings": {"model": "gpt-6-astra",
                                                "model_provider_id": provider}}),
        _line("event_msg", {"type": "user_message",
                            "message": "Tell me the word BANANA."}),
        _line("response_item", {"type": "message", "role": "user",
                                "content": [{"type": "input_text",
                                             "text": "Tell me the word BANANA."}]}),
        _line("response_item", reasoning),
        _line("response_item", {"type": "function_call", "name": "exec",
                                "arguments": "{\"cmd\":\"echo ok\"}",
                                "call_id": "call_1"}),
        _line("response_item", {"type": "function_call_output",
                                "call_id": "call_1", "output": "ok"}),
        _line("response_item", {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text",
                                             "text": "BANANA."}]}),
        _line("event_msg", {"type": "agent_message", "message": "BANANA."}),
        _line("compacted", {
            "message": "",
            "replacement_history": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text",
                              "text": "Tell me the word BANANA."}]},
                {"type": "compaction", "encrypted_content": "gAAAAB-summary"},
            ],
            "guardian_history": [
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "BANANA."}]},
                reasoning,
            ],
            "window_number": 1}),
        _line("response_item", reasoning),
    ]


def write_rollout(home: Path, cwd: str, provider: str = "openai") -> Path:
    """Write the fixture rollout under ``home/sessions/2026/11/03``."""
    day = home / "sessions" / "2026" / "11" / "03"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-2026-11-03T13-26-24-{SID}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n"
                            for r in rollout_records(cwd, provider)))
    return path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    home.mkdir()
    return home


class TestRetarget:
    """Codex -> Codex onto another provider."""

    def test_openai_to_local_rewrites_provider_and_forks(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        before = src.read_bytes()
        new_id, out, stats = retarget_codex_session(src, "local-fractal")

        assert src.read_bytes() == before  # source untouched
        assert out.parent.parent.parent.parent == codex_home / "sessions"
        assert out.name.endswith(f"-{new_id}.jsonl") and new_id != SID
        recs = read_jsonl(out)
        meta = recs[0]["payload"]
        assert recs[0]["type"] == "session_meta"
        assert meta["id"] == meta["session_id"] == new_id
        assert meta["forked_from_id"] == SID
        assert meta["model_provider"] == "local-fractal"
        assert meta["cwd"] == str(tmp_path)
        lineage = recs[1]["payload"]
        assert lineage["id"] == PARENT  # parent's identity is kept
        assert lineage["model_provider"] == "local-fractal"
        cm = recs[0]["continue_metadata"]
        assert cm["parent_session_id"] == SID
        assert (cm["retargeted_from_provider"], cm["retargeted_to_provider"]) \
            == ("openai", "local-fractal")
        settings = [r for r in recs if r["payload"].get("type")
                    == "thread_settings_applied"]
        assert settings[0]["payload"]["thread_settings"]["model_provider_id"] \
            == "local-fractal"
        assert stats.source_provider == "openai"

    def test_encrypted_items_are_stripped_everywhere(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        _, out, stats = retarget_codex_session(src, "local-fractal")
        text = out.read_text()
        assert "encrypted_content" not in text
        assert stats.dropped == {"reasoning": 3, "compaction": 1}
        recs = read_jsonl(out)
        compacted = next(r for r in recs if r["type"] == "compacted")["payload"]
        assert [i["type"] for i in compacted["replacement_history"]] == ["message"]
        assert [i["type"] for i in compacted["guardian_history"]] == ["message"]
        # visible conversation and tool traffic survive, in order
        kinds = [r["payload"].get("type") for r in recs
                 if r["type"] == "response_item"]
        assert kinds == ["message", "function_call", "function_call_output",
                         "message"]
        assert len(recs) == len(rollout_records(str(tmp_path))) - 2

    def test_back_to_openai_drops_local_reasoning(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        _, local, _ = retarget_codex_session(src, "local-fractal")
        # a turn on the local model: vLLM-style reasoning with raw text,
        # which OpenAI rejects as input ("content: array too long")
        with open(local, "a") as f:
            f.write(json.dumps(_line("turn_context", {"cwd": str(tmp_path),
                                                      "model": "qwen3.8-27b"})) + "\n")
            f.write(json.dumps(_line("response_item", {
                "type": "reasoning", "id": "rs_1", "summary": [],
                "content": [{"type": "reasoning_text", "text": "thinking"}],
                "encrypted_content": None})) + "\n")
            f.write(json.dumps(_line("response_item", {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "banana"}]})) + "\n")
        _, back, stats = retarget_codex_session(local, "openai")
        recs = read_jsonl(back)
        assert recs[0]["payload"]["model_provider"] == "openai"
        local_id = read_jsonl(local)[0]["payload"]["id"]
        assert recs[0]["payload"]["forked_from_id"] == local_id
        assert stats.source_provider == "local-fractal"
        assert stats.dropped == {"reasoning": 1}
        assert stats.last_model == "qwen3.8-27b"
        assert not any(r["payload"].get("type") == "reasoning" for r in recs)
        assert recs[-1]["payload"]["content"][0]["text"] == "banana"

    def test_history_entry_appended(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        new_id, _, _ = retarget_codex_session(src, "local-fractal")
        entries = read_jsonl(codex_home / "history.jsonl")
        assert entries[-1]["session_id"] == new_id
        assert entries[-1]["text"] == "Tell me the word BANANA."

    def test_explicit_codex_home_wins(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        other = tmp_path / "other-home"
        _, out, _ = retarget_codex_session(src, "local-fractal", codex_home=other)
        assert other in out.parents
        assert (other / "history.jsonl").exists()
        assert not (codex_home / "history.jsonl").exists()

    def test_archived_rollout_stays_in_its_home(self, codex_home, tmp_path):
        live = write_rollout(codex_home, str(tmp_path))
        archived = codex_home / "archived_sessions" / live.name
        archived.parent.mkdir()
        live.rename(archived)
        _, out, _ = retarget_codex_session(archived, "local-fractal")
        assert out.parents[3] == codex_home / "sessions"

    def test_same_provider_is_refused(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path), provider="local-fractal")
        with pytest.raises(ValueError, match="already uses provider"):
            retarget_codex_session(src, "local-fractal")

    def test_not_a_rollout_is_refused(self, tmp_path):
        bad = tmp_path / "x.jsonl"
        bad.write_text(json.dumps({"type": "user", "message": {}}) + "\n")
        with pytest.raises(ValueError, match="Not a Codex rollout"):
            retarget_codex_session(bad, "local-fractal")

    @pytest.mark.parametrize("bad", ["", "a b", "x;rm", "-x", "a.b"])
    def test_invalid_provider_ids(self, bad):
        with pytest.raises(ValueError, match="Invalid provider"):
            validate_provider(bad)

    def test_service_result(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        result = retarget_service(src, "local-fractal")
        assert result.target_agent == "codex"
        assert result.provider == "local-fractal"
        assert result.resume_hint == (
            f"cd {tmp_path} && CODEX_HOME={codex_home} codex "
            f"-c model_provider=local-fractal resume {result.new_session_id}")
        assert any("3 reasoning" in n for n in result.notes)
        assert any("compaction summary" in n for n in result.notes)
        assert any("last model (gpt-6-astra)" in n for n in result.notes)
        with pytest.raises(PortSessionError):
            retarget_service(src, "openai")  # same provider

    def test_compaction_warning_with_explicit_model(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        result = retarget_service(src, "local-fractal", model="qwen3.8-27b")
        assert any("compaction summary" in n for n in result.notes)
        assert not any("last model" in n for n in result.notes)

    def test_service_model_goes_into_hint(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path), provider="local-fractal")
        result = retarget_service(src, "openai", model="gpt-6.1-sol")
        assert result.resume_hint == (
            f"cd {tmp_path} && CODEX_HOME={codex_home} codex "
            f"-c model_provider=openai -m gpt-6.1-sol "
            f"resume {result.new_session_id}")
        assert not any("last model" in n for n in result.notes)

    def test_default_home_needs_no_codex_home(self, codex_home, tmp_path,
                                               monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        src = write_rollout(codex_home, str(tmp_path))
        result = retarget_service(src, "local-fractal")
        assert "CODEX_HOME=" not in result.resume_hint


class TestClaudeToCodexProvider:
    """Claude -> Codex with an explicit provider."""

    def test_default_stays_openai(self, codex_home, tmp_path):
        claude = write_claude_session(tmp_path / "claude-home", tmp_path)
        result = port_claude_session(claude, codex_home=str(codex_home))
        assert read_jsonl(result.output_file)[0]["payload"]["model_provider"] \
            == "openai"
        assert result.resume_hint.endswith(f"codex resume {result.new_session_id}")

    def test_local_provider(self, codex_home, tmp_path):
        claude = write_claude_session(tmp_path / "claude-home", tmp_path)
        result = port_claude_session(claude, codex_home=str(codex_home),
                                     provider="local-fractal")
        assert read_jsonl(result.output_file)[0]["payload"]["model_provider"] \
            == "local-fractal"
        assert "-c model_provider=local-fractal resume" in result.resume_hint
        assert f"CODEX_HOME={codex_home} codex" in result.resume_hint

    def test_explicit_openai_is_named_in_hint(self, codex_home, tmp_path):
        claude = write_claude_session(tmp_path / "claude-home", tmp_path)
        result = port_claude_session(claude, codex_home=str(codex_home),
                                     provider="openai", model="gpt-6-astra")
        assert "codex -c model_provider=openai -m gpt-6-astra resume" \
            in result.resume_hint

    def test_invalid_provider(self, codex_home, tmp_path):
        claude = write_claude_session(tmp_path / "claude-home", tmp_path)
        with pytest.raises(PortSessionError, match="Invalid provider"):
            port_claude_session(claude, codex_home=str(codex_home),
                                provider="bad id")


class TestCLI:
    """`aichat port SESSION --provider NAME` end to end through Click."""

    def test_codex_to_codex(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        result = CliRunner().invoke(main, [
            "port", str(src), "--provider", "local-fractal",
            "--codex-home", str(codex_home),
            "--claude-home", str(tmp_path / "claude-home")])
        assert result.exit_code == 0, result.output
        assert "Port complete" in result.output
        assert "-c model_provider=local-fractal resume" in result.output
        assert "Dropped provider-specific items" in result.output
        rollouts = list((codex_home / "sessions").rglob("rollout-*.jsonl"))
        assert len(rollouts) == 2  # the source and its re-targeted copy

    def test_codex_without_provider_still_ports_to_claude(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        claude_home = tmp_path / "claude-home"
        result = CliRunner().invoke(main, [
            "port", str(src), "--codex-home", str(codex_home),
            "--claude-home", str(claude_home)])
        assert result.exit_code == 0, result.output
        assert "claude --resume" in result.output

    def test_model_without_provider_is_refused_for_codex(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        result = CliRunner().invoke(main, [
            "port", str(src), "--model", "x", "--codex-home", str(codex_home)])
        assert result.exit_code == 1
        assert "add --provider" in result.output

    def test_bad_provider_exits_nonzero(self, codex_home, tmp_path):
        src = write_rollout(codex_home, str(tmp_path))
        result = CliRunner().invoke(main, [
            "port", str(src), "--provider", "bad id",
            "--codex-home", str(codex_home)])
        assert result.exit_code == 1
        assert "Invalid provider" in result.output


class TerminalInput(StringIO):
    """String input stream that behaves like an interactive terminal."""

    def isatty(self) -> bool:
        """Report that the test input stream is interactive."""
        return True


def test_clipboard_follow_up_keeps_resume_flags(tmp_path, monkeypatch):
    """After copying the id, the suggested command keeps provider and home."""
    capture = tmp_path / "clipboard.txt"
    pbcopy = tmp_path / "pbcopy"
    pbcopy.write_text("#!/bin/sh\n/bin/cat > \"$AICHAT_CLIPBOARD_CAPTURE\"\n")
    pbcopy.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("AICHAT_CLIPBOARD_CAPTURE", str(capture))
    monkeypatch.setattr("sys.stdin", TerminalInput("\n"))
    output = StringIO()
    display = PortDisplay("session")
    display.console = Console(file=output, color_system=None,
                              force_terminal=True, width=160)
    sid = "01a1a2c6-10eb-7f75-9852-9b05d4ac0355"
    cwd = "/work/R&D && examples"  # an ' && ' inside the quoted cwd
    result = PortResult(
        new_session_id=sid, output_file=tmp_path / "x.jsonl", cwd=cwd,
        resume_hint=(f"cd '{cwd}' && CODEX_HOME=/h codex "
                     f"-c model_provider=local-fractal resume {sid}"),
        target_agent="codex", provider="local-fractal")
    display.complete(result, "codex")
    assert capture.read_text() == sid
    assert ("CODEX_HOME=/h codex -c model_provider=local-fractal resume "
            "<paste session ID>") in output.getvalue()
