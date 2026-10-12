"""Runs "S1-mini" by "Superwhisper" in a managed llama.cpp server.

``PostProcessor`` is what the app uses: ``start()`` boots everything in
a background thread (model download, ``llama-server`` launch, warmup)
and ``clean(text)`` returns the cleaned text, or ``text`` unchanged
whenever cleanup is not ready, fails, times out, or produces output
that ``postprocess.rejection_reason`` rejects. It never raises and
never blocks past ``post_process_timeout``.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .postprocess import (
    build_messages,
    max_output_tokens,
    mostly_non_ascii,
    rejection_reason,
    sanitize_output,
    split_chunks,
)

MODEL_NAME = '"S1-mini" by "Superwhisper"'
# Pinned revision + hash (the same pin Freehand STT uses), so a changed
# upstream file can never be loaded silently.
MODEL_REVISION = "34add00a48a2e5d24e5a4ee5405a99620a3a240c"
MODEL_FILE = "s1-mini-q4_k_m.gguf"
MODEL_URL = (
    "https://huggingface.co/superwhisper/s1-mini-GGUF/resolve/"
    f"{MODEL_REVISION}/{MODEL_FILE}"
)
MODEL_SHA256 = (
    "3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634"
)
MODEL_SIZE = 484_219_808
CACHE_DIR = Path.home() / ".cache" / "voxtype"
MODEL_DIR = CACHE_DIR / "models"
# Per-instance pid and log files, named by the OWNING voxtype's pid, so
# several voxtype processes (e.g. dictation plus `voxtype post-process`)
# never clobber or kill each other's servers.
RUN_DIR = CACHE_DIR / "run"
# Passed as --alias so a stale server can be recognized as ours.
SERVER_ALIAS = "voxtype-post"
# Seconds between restart attempts after the server dies.
RESTART_BACKOFF = 60.0

Status = Callable[[str], None]


def default_model_path() -> Path:
    """Where the pinned S1-mini download lives."""
    return MODEL_DIR / MODEL_FILE


def ensure_model(model: str, status: Status) -> Path:
    """Return a usable model path, downloading the default if needed.

    Args:
        model: ``post_process_model`` from the config; empty means the
            pinned S1-mini download.
        status: Progress/status sink.

    Raises:
        FileNotFoundError: A custom model path does not exist.
        ValueError: The download does not match the pinned hash.
        OSError: The download failed.
    """
    if model:
        path = Path(model).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"post_process_model not found: {path}")
        return path
    path = default_model_path()
    # Full hash check (~0.5 s, on the background boot thread): a size
    # match alone would accept a corrupted file.
    if path.is_file() and path.stat().st_size == MODEL_SIZE:
        if _sha256(path) == MODEL_SHA256:
            return path
        status("cached model failed its sha256 check; re-downloading")
    download_model(path, status)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(path: Path, status: Status) -> None:
    """Download the pinned GGUF to ``path``, verifying its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per process: two concurrent downloads (dictation plus
    # `voxtype post-process`) must never write the same partial file.
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    status(
        f"downloading {MODEL_NAME} ({MODEL_SIZE / 2**20:.0f} MiB) "
        f"to {path} ..."
    )
    digest = hashlib.sha256()
    done = 0
    next_report = 0.25
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, open(
            part, "wb"
        ) as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if done / MODEL_SIZE >= next_report:
                    status(f"  ... {int(next_report * 100)}%")
                    next_report += 0.25
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError(
                "downloaded model does not match the pinned sha256; not used"
            )
        # Atomic: a concurrent download replacing it first is harmless,
        # both files having the same verified content.
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    status(f"downloaded {MODEL_NAME}")


def find_server_binary(name: str) -> str | None:
    """Resolve ``post_process_server`` to an executable path, or None."""
    expanded = os.path.expanduser(name)
    if os.sep in expanded:
        return expanded if os.access(expanded, os.X_OK) else None
    return shutil.which(expanded)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _command_of(pid: int) -> str:
    try:
        return subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def pidfile_for(owner: int, run_dir: Path | None = None) -> Path:
    """Pid file recording the server owned by voxtype process ``owner``."""
    return (run_dir or RUN_DIR) / f"llama-server.{owner}.pid"


def logfile_for(owner: int, run_dir: Path | None = None) -> Path:
    """Log file of the server owned by voxtype process ``owner``."""
    return (run_dir or RUN_DIR) / f"llama-server.{owner}.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_stale_servers(run_dir: Path | None = None) -> int:
    """Kill servers whose owning voxtype died; returns how many.

    A server is stale only when the voxtype process that started it is
    gone; servers of live instances are never touched. Only a process
    whose command line carries our ``--alias`` is killed, so a recycled
    server pid is safe too.
    """
    run_dir = run_dir or RUN_DIR
    killed = 0
    for pidfile in run_dir.glob("llama-server.*.pid"):
        try:
            owner = int(pidfile.name.split(".")[1])
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError, IndexError):
            continue
        if owner == os.getpid() or _pid_alive(owner):
            continue
        command = _command_of(pid)
        if "llama-server" in command and SERVER_ALIAS in command:
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except OSError:
                pass
        pidfile.unlink(missing_ok=True)
        logfile_for(owner, run_dir).unlink(missing_ok=True)
    return killed


class LlamaServer:
    """One ``llama-server`` child process serving S1-mini on localhost."""

    def __init__(
        self, binary: str, model_path: Path, run_dir: Path | None = None
    ) -> None:
        self.binary = binary
        self.model_path = model_path
        self.port = 0
        self.proc: subprocess.Popen[bytes] | None = None
        self.pidfile = pidfile_for(os.getpid(), run_dir)
        self.log = logfile_for(os.getpid(), run_dir)
        # Guards proc + _cancelled so stop() from another thread during
        # start() either prevents the launch or terminates the child.
        self._lock = threading.Lock()
        self._cancelled = False

    def command(self) -> list[str]:
        """The launch command (thinking off and greedy, per the card)."""
        return [
            self.binary,
            "-m", str(self.model_path),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--alias", SERVER_ALIAS,
            "--jinja",
            "--chat-template-kwargs", '{"enable_thinking":false}',
            "--temp", "0",
            "-np", "1",
            "-c", "2048",
        ]

    def start(self, timeout: float = 90.0) -> None:
        """Launch and wait until ``/health`` reports the model loaded.

        Raises:
            RuntimeError: The server exited or did not become healthy.
        """
        self.port = _free_port()
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._cancelled:
                raise RuntimeError("llama-server start cancelled")
            with open(self.log, "wb") as log:
                proc = subprocess.Popen(
                    self.command(),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            self.proc = proc
            self.pidfile.write_text(str(proc.pid))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancelled:
                raise RuntimeError("llama-server start cancelled")
            if proc.poll() is not None:
                code = proc.returncode
                self.stop(keep_log=True)
                raise RuntimeError(
                    f"llama-server exited (code {code}); see {self.log}"
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/health", timeout=2
                ) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"llama-server not healthy after {timeout:.0f}s")

    def alive(self) -> bool:
        """True while the child process is running."""
        return self.proc is not None and self.proc.poll() is None

    def complete(
        self, messages: list[dict[str, str]], max_tokens: int, timeout: float
    ) -> tuple[str, str]:
        """One chat completion; returns ``(content, finish_reason)``.

        Raises:
            OSError: Connection failure or timeout (``URLError`` and
                ``TimeoutError`` are both ``OSError``).
            ValueError: A malformed response body.
        """
        body = json.dumps({
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"malformed response: {e}") from e
        if not isinstance(content, str):
            raise ValueError("malformed response: content is not a string")
        return content, str(finish)

    def stop(self, keep_log: bool = False) -> None:
        """Terminate the child (kill after 3 s) and clear the pid file.

        Safe from any thread, including while ``start()`` is running:
        a stop that lands before the launch cancels it.
        """
        with self._lock:
            self._cancelled = True
            proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        self.pidfile.unlink(missing_ok=True)
        if not keep_log:
            self.log.unlink(missing_ok=True)


class PostProcessor:
    """Optional transcript cleanup; every failure returns the raw text."""

    def __init__(
        self,
        *,
        style: str,
        structure: str,
        context: str,
        timeout: float,
        model: str,
        server: str,
        keepalive_minutes: float,
        status: Status,
    ) -> None:
        self.style = style
        self.structure = structure
        self.context = context
        self.timeout = timeout
        self.model = model
        self.server_name = server
        self.keepalive_secs = keepalive_minutes * 60.0
        self.status = status
        self._server: LlamaServer | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        # Guards the boot/restart bookkeeping (held only briefly).
        self._lock = threading.Lock()
        # Serializes requests: the server has a single slot. Cleanup
        # waits for it only within its own deadline.
        self._req_lock = threading.Lock()
        self._booting = False
        self._last_boot = 0.0
        self._last_use = time.monotonic()
        self._warned_not_ready = False

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Boot in the background; returns immediately."""
        self._spawn_boot()
        if self.keepalive_secs > 0:
            threading.Thread(
                target=self._keepalive_loop,
                name="voxtype-post-keepalive",
                daemon=True,
            ).start()

    def _spawn_boot(self) -> None:
        with self._lock:
            if self._booting or self._stop.is_set():
                return
            self._booting = True
            self._last_boot = time.monotonic()
        threading.Thread(
            target=self._boot, name="voxtype-post-boot", daemon=True
        ).start()

    def _boot(self) -> None:
        try:
            if kill_stale_servers():
                self.status("post-process: stopped a stale llama-server")
            binary = find_server_binary(self.server_name)
            if binary is None:
                self.status(
                    f"post-process: {self.server_name!r} not found — "
                    "install llama.cpp (brew install llama.cpp); "
                    "typing raw transcripts"
                )
                return
            model_path = ensure_model(self.model, self.status)
            server = LlamaServer(binary, model_path)
            # Published BEFORE start(): stop() can then cancel a launch
            # still loading the model instead of orphaning it. stop()
            # sets _stop before taking the lock, so either it sees this
            # server or we see _stop here.
            with self._lock:
                if self._stop.is_set():
                    return
                self._server = server
            server.start()
            # Warm the weights and the prompt cache with one real request.
            server.complete(
                build_messages(
                    "okay so this is a test",
                    self.style, self.structure, self.context,
                ),
                32,
                30.0,
            )
            self._ready.set()
            self._last_use = time.monotonic()
            self.status(f"post-process ready ({MODEL_NAME})")
        except Exception as e:
            # A server that started but failed warmup is torn down, so
            # _maybe_restart can try again later instead of seeing a
            # "live" server that never became ready.
            with self._lock:
                failed, self._server = self._server, None
            if failed is not None:
                failed.stop(keep_log=True)
            if not self._stop.is_set():
                self.status(f"post-process unavailable ({e}); typing raw")
        finally:
            with self._lock:
                self._booting = False

    def stop(self) -> None:
        """Stop the server; safe to call more than once."""
        self._stop.set()
        self._ready.clear()
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            server.stop()

    @property
    def ready(self) -> bool:
        """True once the server is up and warm."""
        return self._ready.is_set()

    def wait_ready(self, timeout: float) -> bool:
        """Block until ready (True) or the boot attempt ended (False)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready.wait(0.2):
                return True
            with self._lock:
                if not self._booting:
                    return self._ready.is_set()
        return False

    # -- cleanup ----------------------------------------------------------

    def clean(self, text: str) -> str:
        """Return ``text`` cleaned, or unchanged on any problem.

        Never raises: a transcript must reach the typist whatever goes
        wrong here.
        """
        try:
            return self._clean(text)
        except Exception as e:
            self.status(f"post-process error ({e}); typing raw")
            return text

    def _clean(self, text: str) -> str:
        if not text.strip():
            return text
        if not self._ready.is_set():
            self._maybe_restart()
            if not self._warned_not_ready:
                self._warned_not_ready = True
                self.status("post-process not ready yet; typing raw")
            return text
        if mostly_non_ascii(text):
            self.status("post-process skipped (non-English); typing raw")
            return text
        t0 = time.monotonic()
        deadline = t0 + self.timeout
        out: list[str] = []
        problems: list[str] = []
        if not self._req_lock.acquire(timeout=self.timeout):
            self.status("post-process busy; typing raw")
            return text
        try:
            server = self._server
            for chunk in split_chunks(text):
                remaining = deadline - time.monotonic()
                if server is None or remaining <= 0:
                    problems.append("timed out")
                    out.append(chunk)
                    continue
                cleaned, problem = self._clean_chunk(server, chunk, remaining)
                if problem:
                    problems.append(problem)
                out.append(cleaned)
            self._last_use = time.monotonic()
        finally:
            self._req_lock.release()
        ms = (time.monotonic() - t0) * 1000
        if problems:
            self.status(
                f"post-process fell back to raw ({problems[0]}; {ms:.0f} ms)"
            )
        else:
            self.status(f"post-processed in {ms:.0f} ms")
        return " ".join(out)

    def _clean_chunk(
        self, server: LlamaServer, chunk: str, timeout: float
    ) -> tuple[str, str]:
        """Clean one chunk; returns ``(text, problem)``, problem "" if ok."""
        try:
            content, finish = server.complete(
                build_messages(
                    chunk, self.style, self.structure, self.context
                ),
                max_output_tokens(chunk),
                timeout,
            )
        except (OSError, ValueError, http.client.HTTPException) as e:
            if not server.alive():
                self._mark_dead()
                return chunk, "llama-server died"
            if isinstance(e, TimeoutError) or "timed out" in str(e):
                return chunk, "timed out"
            return chunk, f"request failed: {e}"
        if finish == "length":
            return chunk, "output truncated"
        cleaned = sanitize_output(content)
        reason = rejection_reason(chunk, cleaned)
        if reason:
            return chunk, reason
        return cleaned, ""

    def _mark_dead(self) -> None:
        self._ready.clear()
        self._warned_not_ready = False
        self.status("post-process: llama-server stopped; will restart")

    def _maybe_restart(self) -> None:
        """Re-boot a dead server, at most once per RESTART_BACKOFF."""
        with self._lock:
            server = self._server
            due = time.monotonic() - self._last_boot >= RESTART_BACKOFF
            if self._booting or not due or self._stop.is_set():
                return
            if server is not None and server.alive():
                return
            self._server = None
        if server is not None:
            server.stop()
        self._spawn_boot()

    # -- keepalive --------------------------------------------------------

    def _keepalive_loop(self) -> None:
        """Send a tiny request after ``keepalive_minutes`` of idle time.

        Mirrors the Parakeet keepalive: it keeps the model's pages in
        recent use so macOS does not compress or swap them out, which
        would otherwise stall the first cleanup after a long pause.
        """
        while not self._stop.wait(min(self.keepalive_secs, 60.0)):
            if not self._ready.is_set():
                continue
            if time.monotonic() - self._last_use < self.keepalive_secs:
                continue
            # Never make a real cleanup wait behind the keepalive.
            if not self._req_lock.acquire(blocking=False):
                continue
            try:
                server = self._server
                if server is not None:
                    server.complete(
                        build_messages(
                            "ok", self.style, self.structure, self.context
                        ),
                        8,
                        10.0,
                    )
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                self._last_use = time.monotonic()
                self._req_lock.release()
