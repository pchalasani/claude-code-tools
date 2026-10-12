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
    kill_stale_servers,
    pidfile_for,
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


def _local_model(tmp_path: Path, monkeypatch) -> bytes:
    """Point the pinned download at a small local file:// "model"."""
    import hashlib

    blob = b"fake gguf " * 1000
    src = tmp_path / "upstream.gguf"
    src.write_bytes(blob)
    monkeypatch.setattr(ps, "MODEL_URL", src.as_uri())
    monkeypatch.setattr(ps, "MODEL_SIZE", len(blob))
    monkeypatch.setattr(ps, "MODEL_SHA256", hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(ps, "MODEL_DIR", tmp_path / "models")
    return blob


def test_ensure_model_downloads_and_repairs(tmp_path, monkeypatch) -> None:
    blob = _local_model(tmp_path, monkeypatch)
    path = ensure_model("", print)
    assert path.read_bytes() == blob
    # Same size, wrong bytes: the hash check catches it and re-downloads.
    path.write_bytes(b"X" * len(blob))
    assert ensure_model("", print).read_bytes() == blob
    assert not list((tmp_path / "models").glob("*.part"))


def test_download_rejects_hash_mismatch(tmp_path, monkeypatch) -> None:
    _local_model(tmp_path, monkeypatch)
    monkeypatch.setattr(ps, "MODEL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="sha256"):
        ensure_model("", print)
    assert not list((tmp_path / "models").iterdir())  # no file, no .part


def test_download_cancel_and_dead_partials(tmp_path, monkeypatch) -> None:
    import threading as th

    _local_model(tmp_path, monkeypatch)
    models = tmp_path / "models"
    models.mkdir()
    dead = models / f"{ps.MODEL_FILE}.{_dead_pid()}.part"
    dead.write_bytes(b"half")  # left by a download killed mid-way
    stop = th.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        ensure_model("", print, stop)
    assert not dead.exists()  # dead process's partial reaped
    assert not list(models.iterdir())  # and the cancelled one removed


def test_dead_partials_reaped_when_model_cached(tmp_path, monkeypatch) -> None:
    _local_model(tmp_path, monkeypatch)
    path = ensure_model("", print)  # model now cached
    dead = path.with_name(f"{path.name}.{_dead_pid()}.part")
    dead.write_bytes(b"half")
    assert ensure_model("", print) == path
    assert not dead.exists()


def test_find_server_binary(tmp_path: Path) -> None:
    assert find_server_binary("definitely-not-a-binary-xyz") is None
    assert find_server_binary("sh") == shutil.which("sh")
    exe = tmp_path / "srv"
    exe.write_text("#!/bin/sh\n")
    assert find_server_binary(str(exe)) is None  # not executable
    exe.chmod(0o755)
    assert find_server_binary(str(exe)) == str(exe)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_kill_stale_servers_spares_unrelated_process(tmp_path: Path) -> None:
    """Dead owner, but the recorded pid is not our llama-server."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        pidfile = pidfile_for(_dead_pid(), tmp_path)
        pidfile.write_text(str(proc.pid))
        assert kill_stale_servers(tmp_path) == 0
        assert proc.poll() is None  # still running
        assert not pidfile.exists()  # stale record cleared
    finally:
        proc.kill()
        proc.wait()


def test_kill_stale_escalates_past_ignored_sigterm(tmp_path: Path) -> None:
    """A stale server that ignores SIGTERM is SIGKILLed; the record is
    only cleared once it has really exited."""
    import sys

    # Ignores SIGTERM; carries our alias on its command line.
    script = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, "--alias", ps.SERVER_ALIAS]
    )
    try:
        time.sleep(0.3)  # let it install the handler
        pidfile = pidfile_for(_dead_pid(), tmp_path)
        pidfile.write_text(str(proc.pid))
        t0 = time.monotonic()
        assert kill_stale_servers(tmp_path) == 1
        assert time.monotonic() - t0 < 6
        proc.wait(timeout=5)
        assert not pidfile.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_command_line_not_truncated_in_narrow_terminal(monkeypatch) -> None:
    """The --alias sits at the END of a long command line; a narrow
    COLUMNS must not cut it off."""
    import sys

    monkeypatch.setenv("COLUMNS", "40")
    proc = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)",
        *(["--padding-arg"] * 20), "--alias", ps.SERVER_ALIAS,
    ])
    try:
        time.sleep(0.2)
        assert f"--alias {ps.SERVER_ALIAS}" in ps._command_of(proc.pid)
    finally:
        proc.kill()
        proc.wait()


def test_kill_stale_servers_spares_live_owner(tmp_path: Path) -> None:
    owner = subprocess.Popen(["sleep", "30"])
    try:
        pidfile = pidfile_for(owner.pid, tmp_path)
        pidfile.write_text("1")
        assert kill_stale_servers(tmp_path) == 0
        assert pidfile.exists()  # a live instance's record is untouched
    finally:
        owner.kill()
        owner.wait()


# A real stand-in for llama-server: a tiny HTTP server process taking
# the same flags. FAKE_MODE (read from the "model" file) picks its
# behavior: "slow" never becomes healthy; "badwarm" is healthy but
# fails every completion.
_FAKE_SERVER = """#!{python}
import sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
mode = open(args[args.index("-m") + 1]).read().strip()
if mode == "slow":
    time.sleep(60)
if mode == "stubborn":
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def do_POST(self):
        self.send_response(500); self.end_headers()
HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def _fake_server(tmp_path: Path, mode: str) -> tuple[str, str]:
    import sys

    exe = tmp_path / "fake-llama-server"
    exe.write_text(_FAKE_SERVER.format(python=sys.executable))
    exe.chmod(0o755)
    model = tmp_path / f"{mode}.gguf"
    model.write_text(mode)
    return str(exe), str(model)


def _processor(server: str, model: str, messages: list[str]) -> PostProcessor:
    return PostProcessor(
        style="semi-formal", structure="prose", context="general",
        timeout=1.0, model=model, server=server,
        keepalive_minutes=0.0, status=messages.append,
    )


def test_stop_during_start_leaves_no_server(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ps, "RUN_DIR", tmp_path / "run")
    exe, model = _fake_server(tmp_path, "slow")
    post = _processor(exe, model, [])
    post.start()
    deadline = time.monotonic() + 5
    while post._server is None or post._server.proc is None:
        assert time.monotonic() < deadline, "server never launched"
        time.sleep(0.05)
    child = post._server.proc
    post.stop()
    child.wait(timeout=5)  # terminated, not orphaned
    assert not list((tmp_path / "run").glob("*.pid"))


def test_stop_kills_a_wrapped_server_child(tmp_path) -> None:
    """A non-exec wrapper's llama-server child dies with the wrapper."""
    fake, model = _fake_server(tmp_path, "badwarm")  # healthy server
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(f'#!/bin/sh\n"{fake}" "$@"\n')  # no exec
    wrapper.chmod(0o755)
    server = LlamaServer(str(wrapper), Path(model), tmp_path / "run")
    server.start(timeout=20)
    shell_pid = server.proc.pid
    children = subprocess.run(
        ["pgrep", "-P", str(shell_pid)], capture_output=True, text=True
    ).stdout.split()
    assert children, "wrapper did not spawn the server child"
    child = int(children[0])
    server.stop()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and ps._pid_alive(child):
        time.sleep(0.05)
    assert not ps._pid_alive(child), "server child outlived its wrapper"


@pytest.mark.parametrize("mode", ["badwarm", "stubborn"])
def test_server_dies_when_voxtype_dies(tmp_path, mode: str) -> None:
    """voxtype killed outright (no cleanup runs): its end of the
    supervisor pipe closes, and the server goes with it — escalating to
    SIGKILL for a server that ignores SIGTERM ("stubborn")."""
    import sys

    fake, model = _fake_server(tmp_path, mode)  # both serve /health
    script = tmp_path / "victim.py"
    script.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "from voxtype.postprocess_server import LlamaServer\n"
        f"s = LlamaServer({fake!r}, Path({model!r}), Path({str(tmp_path)!r}))\n"
        "s.start(timeout=20)\n"
        "print(s.proc.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    victim = subprocess.Popen(
        [sys.executable, str(script)], stdout=subprocess.PIPE, text=True
    )
    try:
        leader = int(victim.stdout.readline())
        victim.kill()  # SIGKILL: no finally, no atexit
        victim.wait()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and ps._group_alive(leader):
            time.sleep(0.1)
        assert not ps._group_alive(leader), "server outlived voxtype"
    finally:
        if victim.poll() is None:
            victim.kill()
            victim.wait()


def test_supervisor_exits_cleanly_with_server_status(tmp_path) -> None:
    """Server exits on its own while voxtype lives: the supervisor
    returns the server's status promptly, without aborting."""
    import sys

    sup = subprocess.Popen(
        [sys.executable, "-m", "voxtype._post_supervisor",
         sys.executable, "-c", "import sys; sys.exit(3)"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    t0 = time.monotonic()
    try:
        assert sup.wait(timeout=10) == 3  # not -6 (abort)
        assert time.monotonic() - t0 < 2.5  # no idle 3 s grace wait
    finally:
        if sup.poll() is None:
            sup.kill()
        sup.stdin.close()


def test_backgrounding_wrapper_leaves_no_server(tmp_path) -> None:
    """A wrapper that backgrounds the server and exits: the supervisor
    ends the orphaned server instead of leaving it untracked."""
    fake, model = _fake_server(tmp_path, "badwarm")
    wrapper = tmp_path / "bg-wrapper.sh"
    wrapper.write_text(f'#!/bin/sh\n"{fake}" "$@" &\nsleep 1\n')
    wrapper.chmod(0o755)
    server = LlamaServer(str(wrapper), Path(model), tmp_path / "run")
    try:
        server.start(timeout=20)  # healthy before the wrapper exits
    except RuntimeError:
        pass  # the wrapper may exit first; either way nothing may linger
    leader = server.proc.pid if server.proc else None
    deadline = time.monotonic() + 10
    while leader and time.monotonic() < deadline and ps._group_alive(leader):
        time.sleep(0.1)
    server.stop()
    out = subprocess.run(
        ["pgrep", "-f", str(model)], capture_output=True, text=True
    ).stdout
    assert out.strip() == "", "backgrounded server outlived its wrapper"


def test_warmup_failure_tears_down_for_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ps, "RUN_DIR", tmp_path / "run")
    exe, model = _fake_server(tmp_path, "badwarm")
    messages: list[str] = []
    post = _processor(exe, model, messages)
    post.start()
    assert post.wait_ready(20) is False
    assert post._server is None  # torn down, so a restart can happen
    assert any("unavailable" in m for m in messages)
    assert not list((tmp_path / "run").glob("*.pid"))
    post.stop()


# -- PostProcessor fallbacks against an imitation llama-server ------------


class _Stub:
    """A real HTTP server speaking llama-server's chat-completions API."""

    def __init__(self) -> None:
        self.reply = "Cleaned."
        self.finish = "stop"
        self.delay = 0.0
        self.raw_body: bytes | None = None
        self.truncate = False
        self.body_delay = 0.0
        self.close_conn = False
        self.trickle_headers = 0.0
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
                if stub.trickle_headers:
                    # Raw response, one header line at a time.
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    for i in range(10):
                        self.wfile.write(f"X-Slow-{i}: 1\r\n".encode())
                        self.wfile.flush()
                        time.sleep(stub.trickle_headers)
                    self.wfile.write(
                        f"Content-Length: {len(payload)}\r\n\r\n".encode()
                    )
                    self.wfile.write(payload)
                    return
                if stub.close_conn:
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.close_connection = True
                if stub.truncate:
                    # Promise the full body, send half, then hang up.
                    self.send_header("Content-Length", str(len(payload)))
                    self.close_connection = True
                    payload = payload[: len(payload) // 2]
                self.end_headers()
                self.wfile.flush()
                time.sleep(stub.body_delay)
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


def test_multiline_output_is_typed_on_one_line(stub_post) -> None:
    """A typed newline is the Enter key: it must never reach the typist."""
    post, stub, _ = stub_post
    stub.reply = "1. Install voxtype.\n2. Run setup.\r\n\n3. Start talking."
    out = post.clean("first install voxtype second run setup third start talking")
    assert out == "1. Install voxtype. 2. Run setup. 3. Start talking."


def test_clean_falls_back_on_malformed_response(stub_post) -> None:
    post, stub, _ = stub_post
    stub.raw_body = b'{"nope": 1}'
    assert post.clean("hello there") == "hello there"


def test_connection_close_response_is_accepted(stub_post) -> None:
    post, stub, _ = stub_post
    stub.close_conn = True
    stub.reply = "Hello there."
    assert post.clean("hello there") == "Hello there."
    assert ps.local_request(stub.port, "POST", "/", b"{}", 2.0)[0] == 200


def test_clean_falls_back_on_truncated_response(stub_post) -> None:
    post, stub, _ = stub_post
    stub.truncate = True
    assert post.clean("hello there friend") == "hello there friend"


def test_timeout_is_a_total_deadline(stub_post) -> None:
    """Headers late AND body late: each alone fits the 1.0 s budget,
    together they don't — the take must still fall back on time."""
    post, stub, messages = stub_post
    stub.delay, stub.body_delay = 0.6, 0.6
    t0 = time.monotonic()
    assert post.clean("hello there friend") == "hello there friend"
    assert time.monotonic() - t0 < 1.3
    assert any("timed out" in m for m in messages)


def test_deadline_holds_against_trickled_headers(stub_post) -> None:
    post, stub, messages = stub_post
    stub.trickle_headers = 0.2  # 10 headers, each well within the timeout
    t0 = time.monotonic()
    assert post.clean("hello there friend") == "hello there friend"
    assert time.monotonic() - t0 < 1.3  # timeout is 1.0 s, trickle 2.0 s
    assert any("timed out" in m for m in messages)


def test_proxy_settings_are_ignored(stub_post, monkeypatch) -> None:
    post, stub, _ = stub_post
    for var in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")  # nothing listens
    for var in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    stub.reply = "Hello there."
    assert post.clean("hello there") == "Hello there."


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


def test_clean_waits_for_busy_server_only_within_timeout(stub_post) -> None:
    post, stub, messages = stub_post
    post._req_lock.acquire()  # e.g. a keepalive request in flight
    try:
        t0 = time.monotonic()
        assert post.clean("hello there") == "hello there"
        assert time.monotonic() - t0 < 1.5  # timeout is 1.0 s
    finally:
        post._req_lock.release()
    assert any("busy" in m for m in messages)


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
        self.events: list[str] = []

    def type_text(self, text: str) -> None:
        self.typed.append(text)
        self.events.append(text)

    def press_enter(self) -> None:
        self.enters += 1
        self.events.append("<enter>")


class _Upper:
    """Stands in for PostProcessor in app tests: records and uppercases."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def clean(self, text: str, since: float | None = None) -> str:
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
    app.flush_deliveries()
    assert app._post.seen == ["hello there"]
    assert app.typist.typed == ["HELLO THERE "]
    assert app._session_texts == ["HELLO THERE"]


def test_toggle_off_during_cleanup_still_types(monkeypatch) -> None:
    """A take whose cleanup outlasts the grace window is still typed:
    it arrived (cleanup began) before the toggle-off."""
    app = _vad_app()

    class _Engine:
        def request_flush(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    monkeypatch.setattr(app, "TOGGLE_GRACE", 0.2)

    class _SlowToggle:
        def clean(self, text: str, since: float | None = None) -> str:
            app.toggle()  # user toggles off mid-cleanup
            time.sleep(0.4)  # cleanup outlasts the grace window
            return text.upper()

    app._post = _SlowToggle()
    app.handle_utterance("hello there")
    app.flush_deliveries()
    assert app.typist.typed == ["HELLO THERE "]


class _Slow:
    def __init__(self, secs: float) -> None:
        self.secs = secs

    def clean(self, text: str, since: float | None = None) -> str:
        time.sleep(self.secs)
        return text.upper()


def test_cleanup_never_blocks_the_capture_thread() -> None:
    """handle_utterance runs on the engine's capture thread (VAD): it
    must return at once while cleanup runs elsewhere."""
    app = _vad_app()
    app._post = _Slow(0.5)
    t0 = time.monotonic()
    app.handle_utterance("hello there")
    assert time.monotonic() - t0 < 0.1
    app.flush_deliveries()
    assert app.typist.typed == ["HELLO THERE "]


def test_spoken_submit_waits_for_text_being_cleaned() -> None:
    app = _vad_app()
    app._post = _Slow(0.3)
    app.handle_utterance("fix the bug")
    app.handle_utterance("go")  # arrives while the text is in cleanup
    app.handle_utterance("and add a test")
    app.flush_deliveries()
    assert app.typist.events == [
        "FIX THE BUG ", "<enter>", "AND ADD A TEST "
    ]


def test_queued_text_survives_stop_phrase() -> None:
    """Text already dictated is typed even if "stop listening" follows
    while it is still in cleanup — as inline typing would have."""
    app = _vad_app()
    app._post = _Slow(0.3)
    app.handle_utterance("first sentence")
    app.handle_utterance("second sentence")
    app.handle_utterance("stop listening")
    app.flush_deliveries()
    assert app.typist.typed == ["FIRST SENTENCE ", "SECOND SENTENCE "]


def test_late_take_stays_in_its_own_session() -> None:
    app = _vad_app()

    class _Engine:
        def request_flush(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    app._post = _Slow(0.3)
    app.handle_utterance("old session")  # still in cleanup ...
    app.toggle()  # ... when the user stops
    app._last_toggle = 0.0  # (bypass the toggle debounce)
    app.toggle()  # and starts a new session
    app.flush_deliveries()
    app.note_activity()  # the engine hears the new speech first
    app.handle_utterance("new session")
    app.flush_deliveries()
    assert app.typist.typed[0] == "OLD SESSION "
    assert app._session_texts == ["NEW SESSION"]


def test_rejected_take_never_claims_a_later_handoff() -> None:
    """A take whose state went stale before it was queued stays
    rejected: it must not type via a LATER toggle-off's grace window."""
    app = _vad_app()
    app._post = _Slow(0.3)
    app.handle_utterance("blocker")  # occupies the delivery thread
    with app._lock:
        stale = app._state_version
        app._state_version += 1  # e.g. a cancel landed after the snapshot
        app._grace_until = 0.0
    app._deliver(stale, lambda v, at: app._type("cancelled", v, at))
    with app._lock:
        app._grace_until = time.monotonic() + 5  # a later toggle-off
    app.flush_deliveries()
    assert "CANCELLED " not in app.typist.typed
    assert app._consume_handoff()  # the later handoff is still armed


def test_cancel_never_discards_delivered_text() -> None:
    """As on origin/main — where a delivered utterance is typed at once —
    Esc discards only untranscribed audio, never text already delivered
    and merely waiting for (or in) cleanup."""
    app = _vad_app()

    class _Engine:
        def request_flush(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    app._post = _Slow(0.3)
    app.handle_utterance("first")  # in cleanup ...
    app.handle_utterance("second")  # ... this one waits behind it
    app.cancel()  # Esc while both are pending
    app.flush_deliveries()
    assert app.typist.typed == ["FIRST ", "SECOND "]


def test_cancel_spares_earlier_session_still_in_cleanup() -> None:
    app = _vad_app()

    class _Engine:
        def request_flush(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    app._post = _Slow(0.4)
    app.handle_utterance("keep me")  # session 1, in cleanup
    app.toggle()  # stop session 1
    app._last_toggle = 0.0  # (bypass the toggle debounce)
    app.toggle()  # start session 2
    app.cancel()  # Esc cancels session 2 only
    app.flush_deliveries()
    assert app.typist.typed == ["KEEP ME "]


def test_late_hold_take_belongs_to_previous_session() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(
        Config(mode="toggle", segmentation="hold", sounds=False)
    )
    app.typist = _Typist()

    class _Engine:
        def request_hold_start(self) -> None:
            pass

        def request_hold_stop(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    app._post = _Slow(0.2)
    app.toggle()  # session 1 on
    app._last_toggle = 0.0
    app.toggle()  # session 1 off (take 1 decoding)
    app._last_toggle = 0.0
    app.toggle()  # session 2 on, before take 1 arrives
    app.handle_utterance("take one")  # take 1's late decode
    app.cancel()  # Esc cancels session 2 only
    app.flush_deliveries()
    assert app.typist.typed == ["TAKE ONE "]


def test_late_hold_take_survives_cancelled_middle_session() -> None:
    """Take 1 decodes late; session 2 is cancelled; it arrives during
    session 3. It must still be typed (as on main)."""
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(
        Config(mode="toggle", segmentation="hold", sounds=False)
    )
    app.typist = _Typist()

    class _Engine:
        def request_hold_start(self) -> None:
            pass

        def request_hold_stop(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    app._post = _Slow(0.1)
    for _ in range(2):  # session 1 on, off
        app._last_toggle = 0.0
        app.toggle()
    app._last_toggle = 0.0
    app.toggle()  # session 2 on
    app.cancel()  # session 2 cancelled
    app._last_toggle = 0.0
    app.toggle()  # session 3 on
    app.handle_utterance("take one")  # take 1 arrives now
    app.flush_deliveries()
    assert app.typist.typed == ["TAKE ONE "]


def test_late_vad_flush_belongs_to_previous_session() -> None:
    """VAD: toggle off, quickly back on; the flushed segment lands in the
    new session. It must survive that session's Esc."""
    app = _vad_app()

    class _Engine:
        def request_flush(self) -> None:
            pass

        def request_reset(self) -> None:
            pass

    app._engine = _Engine()
    app._post = _Slow(0.2)
    app.toggle()  # off: arms the in-flight handoff
    app._last_toggle = 0.0
    app.toggle()  # quickly back on
    app.handle_utterance("flushed segment")  # the old session's flush
    app.cancel()  # Esc in the new session
    app.flush_deliveries()
    assert app.typist.typed == ["FLUSHED SEGMENT "]


def test_queue_wait_counts_against_timeout(stub_post) -> None:
    post, stub, messages = stub_post
    # Arrived 2 s ago with a 1 s budget: no request at all, raw at once.
    t0 = time.monotonic()
    assert post.clean("hello there", since=t0 - 2.0) == "hello there"
    assert time.monotonic() - t0 < 0.1
    assert stub.requests == []
    assert any("waited too long" in m for m in messages)


def test_without_cleanup_delivery_stays_inline() -> None:
    app = _vad_app()
    app._post = None
    app.handle_utterance("hello there")
    assert app.typist.typed == ["hello there "]  # no flush needed
    assert app._delivery_q is None


def test_wake_idle_expiry_waits_for_cleanup() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(mode="wake", sounds=False, idle_timeout=0.1))
    app.typist = _Typist()

    class _SlowIdle:
        def clean(self, text: str, since: float | None = None) -> str:
            time.sleep(0.3)  # longer than idle_timeout
            app._check_idle_timeout()  # the idle watcher fires meanwhile
            return text.upper()

    app._post = _SlowIdle()
    app.handle_utterance("hey claude write the summary")
    app.flush_deliveries()
    assert app.typist.typed == ["WRITE THE SUMMARY "]


def test_app_matches_phrases_on_raw_text() -> None:
    app = _vad_app()
    app.handle_utterance("go")
    app.flush_deliveries()
    assert app.typist.enters == 1
    app.handle_utterance("stop listening")
    app.flush_deliveries()
    assert app._post.seen == []
    assert app.typist.typed == []


# -- the real model (skipped unless llama-server + S1-mini are present) ----

_REAL = find_server_binary("llama-server") is not None and (
    ps.default_model_path().is_file()
)


@pytest.mark.skipif(not _REAL, reason="llama-server or S1-mini not installed")
def test_real_s1_mini_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ps, "RUN_DIR", tmp_path)
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
    assert not list(tmp_path.glob("*.pid"))


@pytest.mark.skipif(not _REAL, reason="llama-server or S1-mini not installed")
def test_real_stale_server_is_killed(tmp_path: Path) -> None:
    server = LlamaServer(
        find_server_binary("llama-server"), ps.default_model_path(), tmp_path
    )
    server.start()
    proc = server.proc
    try:
        # A live owner (this test process) keeps its server.
        assert kill_stale_servers(tmp_path) == 0
        assert proc.poll() is None
        # Simulate a crash: re-file the record under a dead owner's pid.
        dead = pidfile_for(_dead_pid(), tmp_path)
        server.pidfile.rename(dead)
        assert kill_stale_servers(tmp_path) == 1
        proc.wait(timeout=10)
        assert not dead.exists()
    finally:
        server.stop()
