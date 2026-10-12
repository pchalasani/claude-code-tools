"""Tests for optional LLM post-processing ("S1-mini" by "Superwhisper").

Three layers: the pure prompt/validation/chunking logic; PostProcessor
fallbacks against a real local HTTP server that imitates llama-server's
API; and, when llama-server and the model are installed, the real thing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from voxtype import postprocess_server as ps
from voxtype.config import Config, load_config
from voxtype.postprocess import (
    SYSTEM_PROMPT,
    build_messages,
    control_line,
    max_output_tokens,
    mostly_non_ascii,
    rejection_reason,
    sanitize_output,
    split_chunks,
)
from voxtype.postprocess_server import (
    LlamaServer,
    PostProcessor,
    ensure_model,
    find_server_binary,
    kill_stale_server,
)

# -- pure logic ------------------------------------------------------------


def test_system_prompt_is_verbatim_from_model_card() -> None:
    assert SYSTEM_PROMPT == (
        "You are a text normalizer for speech-to-text transcripts. The "
        "input begins with a control line specifying the styling, "
        "structure, and context settings; clean the transcript to match "
        "those settings and output only the cleaned text."
    )


def test_build_messages_shape() -> None:
    msgs = build_messages("hello there", "formal", "lists", "email")
    assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == (
        "[Styling: formal] [Structure: lists] [Context: email]\nhello there"
    )
    assert control_line("casual", "prose", "general") == (
        "[Styling: casual] [Structure: prose] [Context: general]"
    )


def test_max_output_tokens_grows_with_input() -> None:
    assert max_output_tokens("go") >= 32
    assert max_output_tokens("word " * 300) > 1.3 * 300


def test_sanitize_output_strips_template_debris() -> None:
    assert sanitize_output("<think>\n\n</think>\n\nHello.") == "Hello."
    assert sanitize_output("Hello.<|im_end|>") == "Hello."
    assert sanitize_output("```\nHello.\n```") == "Hello."
    assert sanitize_output("Output: Hello.") == "Hello."
    assert sanitize_output("  Hello, world.  ") == "Hello, world."


@pytest.mark.parametrize(
    ("raw", "cleaned", "expected"),
    [
        ("so i think we should go", "So I think we should go.", None),
        ("hello there friend", "", "empty"),
        ("hello there friend", "...", "punctuation"),
        (
            "fix the bug",
            "The user is asking to fix the bug.",
            "assistant chatter",
        ),
        ("fix it", "x" * 81, "much longer"),
        ("a" * 60, "a" * 15, "lost most"),
    ],
)
def test_rejection_reason(raw: str, cleaned: str, expected: str | None) -> None:
    reason = rejection_reason(raw, cleaned)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and expected in reason


def test_marker_already_in_raw_is_not_chatter() -> None:
    raw = "write the analysis: revenue grew"
    assert rejection_reason(raw, "Write the analysis: revenue grew.") is None


def test_mostly_non_ascii() -> None:
    assert not mostly_non_ascii("hello café")
    assert mostly_non_ascii("Привет, как дела")
    assert not mostly_non_ascii("123 456")


def test_split_chunks_short_text_is_one_chunk() -> None:
    assert split_chunks("Hello there.") == ["Hello there."]
    assert split_chunks("   ") == []


def test_split_chunks_respects_sentences_and_limit() -> None:
    sentence = " ".join(["word"] * 9) + "."  # 10 words
    text = " ".join([sentence] * 7)  # 70 words
    chunks = split_chunks(text, max_words=25)
    assert all(len(c.split()) <= 25 for c in chunks)
    assert all(c.endswith(".") for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_split_chunks_breaks_overlong_sentence() -> None:
    text = " ".join(f"w{i}" for i in range(60))  # no punctuation at all
    chunks = split_chunks(text, max_words=25)
    assert [len(c.split()) for c in chunks] == [25, 25, 10]
    assert " ".join(chunks).split() == text.split()


# -- config ----------------------------------------------------------------


def test_post_process_defaults_off() -> None:
    cfg = Config()
    cfg.validate()
    assert cfg.post_process is False
    assert cfg.post_process_style == "semi-formal"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("post_process", "yes"),
        ("post_process_style", "chatty"),
        ("post_process_structure", "bullets"),
        ("post_process_context", "slack"),
        ("post_process_timeout", 0.1),
        ("post_process_timeout", float("nan")),
        ("post_process_timeout", True),
        ("post_process_server", ""),
        ("post_process_model", 3),
    ],
)
def test_post_process_validation(key: str, value: object) -> None:
    with pytest.raises(ValueError):
        Config(**{key: value}).validate()


def test_post_process_config_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        'post_process = true\npost_process_style = "formal"\n'
        "post_process_timeout = 2.5\n"
    )
    cfg = load_config(path)
    assert cfg.post_process and cfg.post_process_style == "formal"
    assert cfg.post_process_timeout == 2.5


def test_ensure_model_custom_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ensure_model(str(tmp_path / "missing.gguf"), print)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    assert ensure_model(str(model), print) == model


def test_find_server_binary(tmp_path: Path) -> None:
    assert find_server_binary("definitely-not-a-binary-xyz") is None
    assert find_server_binary("sh") == shutil.which("sh")
    exe = tmp_path / "srv"
    exe.write_text("#!/bin/sh\n")
    assert find_server_binary(str(exe)) is None  # not executable
    exe.chmod(0o755)
    assert find_server_binary(str(exe)) == str(exe)


def test_kill_stale_server_spares_unrelated_process(tmp_path: Path) -> None:
    pidfile = tmp_path / "srv.pid"
    proc = subprocess.Popen(["sleep", "30"])
    try:
        pidfile.write_text(str(proc.pid))
        assert kill_stale_server(pidfile) is False
        assert proc.poll() is None  # still running
        assert not pidfile.exists()
    finally:
        proc.kill()
        proc.wait()


# -- PostProcessor fallbacks against an imitation llama-server ------------


class _Stub:
    """A real HTTP server speaking llama-server's chat-completions API."""

    def __init__(self) -> None:
        self.reply = "Cleaned."
        self.finish = "stop"
        self.delay = 0.0
        self.raw_body: bytes | None = None
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(size))
                stub.requests.append(body)
                time.sleep(stub.delay)
                reply = stub.reply
                if callable(reply):
                    reply = reply(body["messages"][1]["content"])
                payload = stub.raw_body or json.dumps({
                    "choices": [{
                        "message": {"content": reply},
                        "finish_reason": stub.finish,
                    }]
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def stub_post():
    """A ready PostProcessor wired to a _Stub, plus a live child process
    standing in for llama-server's pid (so ``alive()`` is real)."""
    stub = _Stub()
    child = subprocess.Popen(["sleep", "60"])
    messages: list[str] = []
    post = PostProcessor(
        style="semi-formal",
        structure="prose",
        context="general",
        timeout=1.0,
        model="",
        server="llama-server",
        keepalive_minutes=0.0,
        status=messages.append,
    )
    server = LlamaServer("llama-server", Path("unused.gguf"))
    server.port = stub.port
    server.proc = child
    post._server = server
    post._ready.set()
    post._last_boot = time.monotonic()  # no restart attempts in tests
    yield post, stub, messages
    stub.close()
    child.kill()
    child.wait()


def test_clean_uses_model_output(stub_post) -> None:
    post, stub, messages = stub_post
    stub.reply = "So I think we should go."
    assert post.clean("so i think we should go") == "So I think we should go."
    req = stub.requests[0]
    assert req["temperature"] == 0
    assert req["messages"][0]["content"] == SYSTEM_PROMPT
    assert req["messages"][1]["content"].endswith("\nso i think we should go")
    assert any("post-processed in" in m for m in messages)


@pytest.mark.parametrize(
    ("reply", "finish"),
    [
        ("", "stop"),
        ("The user is asking about a meeting.", "stop"),
        ("So I think we should", "length"),
    ],
)
def test_clean_falls_back_on_bad_output(stub_post, reply, finish) -> None:
    post, stub, messages = stub_post
    stub.reply, stub.finish = reply, finish
    raw = "so i think we should move the meeting"
    assert post.clean(raw) == raw
    assert any("fell back to raw" in m for m in messages)


def test_clean_falls_back_on_malformed_response(stub_post) -> None:
    post, stub, _ = stub_post
    stub.raw_body = b'{"nope": 1}'
    assert post.clean("hello there") == "hello there"


def test_clean_times_out_quickly(stub_post) -> None:
    post, stub, messages = stub_post
    stub.delay = 3.0
    t0 = time.monotonic()
    assert post.clean("hello there friend") == "hello there friend"
    assert time.monotonic() - t0 < 2.0  # timeout is 1.0 s
    assert any("timed out" in m for m in messages)


def test_clean_when_server_dies(stub_post) -> None:
    post, stub, messages = stub_post
    stub.close()
    post._server.proc.kill()
    post._server.proc.wait()
    assert post.clean("hello there") == "hello there"
    assert not post.ready
    assert any("died" in m for m in messages)


def test_clean_not_ready_types_raw() -> None:
    messages: list[str] = []
    post = PostProcessor(
        style="semi-formal", structure="prose", context="general",
        timeout=1.0, model="", server="llama-server",
        keepalive_minutes=0.0, status=messages.append,
    )
    post._last_boot = time.monotonic()  # suppress a background boot
    assert post.clean("hello there") == "hello there"
    assert post.clean("again") == "again"
    assert sum("not ready" in m for m in messages) == 1  # warned once


def test_clean_skips_non_english(stub_post) -> None:
    post, stub, _ = stub_post
    assert post.clean("Привет, как дела") == "Привет, как дела"
    assert stub.requests == []


def test_clean_chunks_long_takes(stub_post) -> None:
    post, stub, _ = stub_post
    stub.reply = lambda content: content.split("\n", 1)[1].upper()
    sentence = " ".join(["word"] * 49) + "."  # 50 words
    text = " ".join([sentence] * 12)  # 600 words
    out = post.clean(text)
    assert len(stub.requests) == 3  # 250 + 250 + 100 words
    assert out == text.upper()


def test_failed_chunk_keeps_its_raw_text(stub_post) -> None:
    post, stub, _ = stub_post
    calls = {"n": 0}

    def reply(content: str) -> str:
        calls["n"] += 1
        body = content.split("\n", 1)[1]
        return "" if calls["n"] == 2 else body.upper()

    stub.reply = reply
    first = " ".join(["alpha"] * 249) + "."
    second = " ".join(["beta"] * 249) + "."
    out = post.clean(f"{first} {second}")
    assert out == f"{first.upper()} {second}"


def test_keepalive_pings_idle_server(stub_post) -> None:
    post, stub, _ = stub_post
    post.keepalive_secs = 0.3
    post._last_use = time.monotonic() - 1.0  # already idle
    threading.Thread(target=post._keepalive_loop, daemon=True).start()
    try:
        deadline = time.monotonic() + 3.0
        while not stub.requests and time.monotonic() < deadline:
            time.sleep(0.05)
        assert stub.requests, "keepalive never pinged"
        assert stub.requests[0]["max_tokens"] == 8
    finally:
        post._stop.set()


# -- app wiring ------------------------------------------------------------


class _Typist:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.enters = 0

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def press_enter(self) -> None:
        self.enters += 1


class _Upper:
    """Stands in for PostProcessor in app tests: records and uppercases."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def clean(self, text: str) -> str:
        self.seen.append(text)
        return text.upper()


def _vad_app():
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(mode="vad", sounds=False))
    app.typist = _Typist()
    app._post = _Upper()
    return app


def test_app_cleans_after_filler_removal() -> None:
    app = _vad_app()
    app.handle_utterance("um hello there")
    assert app._post.seen == ["hello there"]
    assert app.typist.typed == ["HELLO THERE "]
    assert app._session_texts == ["HELLO THERE"]


def test_app_matches_phrases_on_raw_text() -> None:
    app = _vad_app()
    app.handle_utterance("go")
    assert app.typist.enters == 1
    app.handle_utterance("stop listening")
    assert app._post.seen == []
    assert app.typist.typed == []


# -- the real model (skipped unless llama-server + S1-mini are present) ----

_REAL = find_server_binary("llama-server") is not None and (
    ps.default_model_path().is_file()
)


@pytest.mark.skipif(not _REAL, reason="llama-server or S1-mini not installed")
def test_real_s1_mini_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ps, "PIDFILE", tmp_path / "srv.pid")
    monkeypatch.setattr(ps, "SERVER_LOG", tmp_path / "srv.log")
    messages: list[str] = []
    post = PostProcessor(
        style="semi-formal", structure="prose", context="general",
        timeout=10.0, model="", server="llama-server",
        keepalive_minutes=0.0, status=messages.append,
    )
    post.start()
    try:
        assert post.wait_ready(120), messages
        pid = post._server.proc.pid
        out = post.clean(
            "so the bug is in the loader it crashes on missing keys "
            "I mean optional keys"
        )
        assert out[0].isupper() and out.endswith("."), out
        assert "optional keys" in out
    finally:
        post.stop()
    assert subprocess.run(["kill", "-0", str(pid)]).returncode != 0
    assert not (tmp_path / "srv.pid").exists()


@pytest.mark.skipif(not _REAL, reason="llama-server or S1-mini not installed")
def test_real_stale_server_is_killed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ps, "PIDFILE", tmp_path / "srv.pid")
    monkeypatch.setattr(ps, "SERVER_LOG", tmp_path / "srv.log")
    server = LlamaServer(
        find_server_binary("llama-server"), ps.default_model_path()
    )
    server.start()
    proc = server.proc
    try:
        # Simulate a crash: voxtype is gone, the pid file remains.
        assert kill_stale_server(tmp_path / "srv.pid") is True
        proc.wait(timeout=10)
    finally:
        server.stop()
