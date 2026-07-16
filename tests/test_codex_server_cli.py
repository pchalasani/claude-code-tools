"""CLI and recovery tests for the Codex app-server helper."""

from __future__ import annotations

import json
import os
import pty
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, NoReturn, cast

import pytest
from click.testing import CliRunner

import claude_code_tools.codex_server as codex_server
import claude_code_tools.codex_server_cli as server_cli_module
import claude_code_tools.codex_server_models as server_models
from claude_code_tools.codex_server_generation import server_generation
from claude_code_tools.codex_server import (
    CodexServerError,
    _paths,
    ensure_server,
    get_status,
    restart_server,
)
from claude_code_tools.codex_server_cli import _log_anchor, _log_was_rewritten
from tests.test_codex_server import (
    _invoke,
    _wait_for_socket,
    process_identity_without_ps as _process_identity_without_ps_fixture,
    server_environment as _server_environment_fixture,
)

_IMPORTED_FIXTURES = (
    _process_identity_without_ps_fixture,
    _server_environment_fixture,
)


def _desired_paths(environment: dict[str, str]) -> server_models.ServerPaths:
    """Resolve the generation a fresh server launch would select."""
    base = server_models.base_paths_from_env(environment)
    codex_path = codex_server._resolve_codex(environment)
    identity = codex_server._codex_executable_identity(codex_path)
    child_env = codex_server._command_env(environment, base)
    version = codex_server._require_compatible_codex(codex_path, child_env)
    snapshot = codex_server._plugin_configuration_snapshot(base)
    generation = server_generation(
        codex_path,
        identity,
        version,
        snapshot.fingerprint,
        (),
    )
    return server_models.paths_for_generation(base, generation)


@pytest.fixture(name="server_environment")
def _server_environment_alias(
    request: pytest.FixtureRequest,
) -> tuple[Path, dict[str, str]]:
    """Expose the shared isolated server fixture in this test module."""
    return cast(
        tuple[Path, dict[str, str]],
        request.getfixturevalue("_server_environment_fixture"),
    )


@pytest.fixture(name="process_identity_without_ps")
def _process_identity_without_ps_alias(
    request: pytest.FixtureRequest,
) -> None:
    """Expose the shared sandbox-safe identity fixture in this module."""
    assert request.getfixturevalue("_process_identity_without_ps_fixture") is None


def test_codex_0136_is_the_oldest_supported_callback_release(
    server_environment: tuple[Path, dict[str, str]],
    process_identity_without_ps: None,
) -> None:
    """The complete callback protocol floor is enforced before startup."""
    root, environment = server_environment
    environment["FAKE_CODEX_VERSION"] = "codex-cli 0.135.0"

    with pytest.raises(CodexServerError, match=r"0\.136\.0 or newer"):
        ensure_server(environment)
    assert not (root / "launches.txt").exists()

    arguments_path = root / "arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(arguments_path)
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
    ]
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert "0.136.0 or newer" in result.stderr
    assert not arguments_path.exists()

    environment["FAKE_CODEX_VERSION"] = "codex-cli 0.136.0"
    assert ensure_server(environment).status == "running"


def test_log_follower_detects_rapid_truncate_and_regrowth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation-prefix checks close the follower's suffix-anchor ABA hole."""
    path = tmp_path / "server.log"
    suffix = b"preserved-suffix" * 4
    common_generation_prefix = "same-prefix-"
    generations = iter(
        (
            common_generation_prefix + ("1" * 20),
            common_generation_prefix + ("2" * 20),
        )
    )

    def next_generation(_byte_count: int) -> str:
        return next(generations)

    monkeypatch.setattr(server_models, "LOG_MAX_BYTES", 512)
    monkeypatch.setattr(server_models.secrets, "token_hex", next_generation)
    path.write_bytes(b"old" * 166)
    with server_models.open_log_append(path) as writer:
        server_models.write_bounded_log(writer, b"A" * 200 + suffix)
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            anchor = _log_anchor(stream)
            server_models.write_bounded_log(
                writer,
                b"B" * 200 + suffix + b"C" * 100,
            )

            assert os.pread(stream.fileno(), 64, 0) == anchor[1][:64]
            suffix_start = anchor[0] - len(anchor[2])
            assert os.pread(stream.fileno(), len(anchor[2]), suffix_start) == anchor[2]
            assert _log_was_rewritten(stream, anchor)


def test_log_follow_reads_bytes_appended_after_tail_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One descriptor and snapshot EOF cover the former tail/follow gap."""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    environment = {"CODEX_HOME": str(codex_home)}
    paths = _paths(environment)
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    paths.log_path.write_bytes(b"initial\n")
    original_tail = server_cli_module._log_tail_stream

    def append_after_tail(
        stream: BinaryIO,
        lines: int,
    ) -> server_models.LogTailSnapshot:
        result = original_tail(stream, lines)
        with paths.log_path.open("ab") as writer:
            writer.write(b"between-tail-and-follow\n")
        return result

    def interrupt_wait(_delay: float) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_cli_module, "_log_tail_stream", append_after_tail)
    monkeypatch.setattr(server_cli_module.time, "sleep", interrupt_wait)

    result = CliRunner().invoke(
        server_cli_module.server_cli,
        ["logs", "--follow"],
        env=environment,
    )

    assert result.exit_code == 0, result.output
    assert result.output == "initial\nbetween-tail-and-follow\n"


def test_log_follow_detects_rewrite_after_tail_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tail snapshot's generation guards follow setup itself."""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    environment = {"CODEX_HOME": str(codex_home)}
    paths = _paths(environment)
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    paths.log_path.write_bytes(b"x" * 110 + b"\nold-tail\n")
    original_tail = server_cli_module._log_tail_stream

    def rewrite_after_tail(
        stream: BinaryIO,
        lines: int,
    ) -> server_models.LogTailSnapshot:
        snapshot = original_tail(stream, lines)
        with server_models.open_log_append(paths.log_path) as writer:
            server_models.write_bounded_log(
                writer,
                b"new-after-rewrite\n" * 3,
            )
        return snapshot

    def interrupt_wait(_delay: float) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_models, "LOG_MAX_BYTES", 128)
    monkeypatch.setattr(
        server_models.secrets,
        "token_hex",
        lambda _byte_count: "3" * 32,
    )
    monkeypatch.setattr(server_cli_module, "_log_tail_stream", rewrite_after_tail)
    monkeypatch.setattr(server_cli_module.time, "sleep", interrupt_wait)

    result = CliRunner().invoke(
        server_cli_module.server_cli,
        ["logs", "--lines", "1", "--follow"],
        env=environment,
    )

    assert result.exit_code == 0, result.output
    assert result.output.startswith("old-tail\n")
    assert "codex-server truncated an oversized log" in result.output
    assert "new-after-rewrite" in result.output


def test_concurrent_start_launches_one_server(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """The lifecycle lock prevents duplicate servers across processes."""
    root, environment = server_environment
    command = [
        sys.executable,
        "-m",
        "claude_code_tools.codex_server_cli",
        "start",
        "--json",
    ]
    processes = [
        subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    results = [process.communicate(timeout=15) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    pids = {json.loads(stdout)["pid"] for stdout, _stderr in results}
    assert len(pids) == 1
    launches = (root / "launches.txt").read_text(encoding="utf-8").splitlines()
    assert len(launches) == 1


def test_codex_dynamic_starts_server_and_forwards_resume_arguments(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """The remote wrapper execs Codex with unchanged subcommand arguments."""
    root, environment = server_environment
    arguments_path = root / "arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(arguments_path)
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
        "resume",
        "--last",
    ]
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    invocation = json.loads(arguments_path.read_text(encoding="utf-8"))
    endpoint = invocation["callbackEndpoint"]
    assert endpoint.startswith("unix://")
    assert endpoint.endswith(".sock")
    assert invocation["args"] == [
        "--config",
        (
            "shell_environment_policy.set.CCTOOLS_CODEX_CALLBACK_ENDPOINT="
            f"{json.dumps(endpoint)}"
        ),
        "--remote",
        endpoint,
        "resume",
        "--last",
    ]
    assert Path(invocation["codexHome"]) == Path(environment["CODEX_HOME"]).resolve()
    assert get_status(environment).status == "running"


def test_codex_dynamic_rolls_plugins_without_disconnecting_old_session(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Two wrapper launches can use different live plugin generations."""
    root, environment = server_environment
    first_arguments = root / "first-arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(first_arguments)
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
    ]
    first_result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert first_result.returncode == 0, first_result.stderr
    first_invocation = json.loads(first_arguments.read_text(encoding="utf-8"))
    first_status = get_status(environment)

    config_path = first_status.paths.codex_home / "config.toml"
    config_path.write_text(
        '[plugins."sample@example"]\nenabled = true\n',
        encoding="utf-8",
    )
    second_arguments = root / "second-arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(second_arguments)
    second_result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert second_result.returncode == 0, second_result.stderr
    second_invocation = json.loads(second_arguments.read_text(encoding="utf-8"))
    second_status = get_status(environment)

    assert first_invocation["callbackEndpoint"] != second_invocation["callbackEndpoint"]
    assert first_invocation["args"][3] == first_invocation["callbackEndpoint"]
    assert second_invocation["args"][3] == second_invocation["callbackEndpoint"]
    assert first_status.pid != second_status.pid
    assert codex_server._process_group_exists(first_status.pid or 0)
    _wait_for_socket(first_status.paths.socket_path)


def test_codex_dynamic_propagates_plugin_configuration_to_server(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Every plugin-affecting global override reaches the certified worker."""
    root, environment = server_environment
    server_arguments = root / "server-arguments.json"
    environment["FAKE_CODEX_SERVER_ARGS"] = str(server_arguments)
    overrides = [
        "--enable",
        "plugins",
        "--disable=remote_plugin",
        "-c",
        "features.plugin_sharing=true",
        "--config=plugins.sample.enabled=true",
        "--profile",
        "callbacks",
    ]
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
        *overrides,
    ]

    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(server_arguments.read_text(encoding="utf-8")) == overrides


def test_forced_restart_preserves_certified_server_options(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """A forced restart relaunches with the exact certified option snapshot."""
    root, environment = server_environment
    server_arguments = root / "server-arguments.json"
    environment["FAKE_CODEX_SERVER_ARGS"] = str(server_arguments)
    options = [
        "--profile",
        "callbacks",
        "--enable",
        "apps",
        "--disable",
        "remote_plugin",
        "-c",
        "features.plugin_sharing=true",
    ]
    first = ensure_server(environment, codex_options=options)

    restarted = restart_server(environment, allow_disconnect=True)

    assert restarted.pid != first.pid
    assert json.loads(server_arguments.read_text(encoding="utf-8")) == options


def test_codex_dynamic_rejects_endpoint_override(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Users cannot accidentally split the helper and TUI endpoints."""
    root, environment = server_environment
    arguments_path = root / "arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(arguments_path)
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
        "--remote",
        "unix:///tmp/other.sock",
    ]
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "owns --remote" in result.stderr
    assert not arguments_path.exists()
    assert get_status(environment).status == "stopped"


def test_codex_dynamic_help_does_not_start_server(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Informational Codex commands do not create a background process."""
    root, environment = server_environment
    arguments_path = root / "arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(arguments_path)
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
        "--help",
    ]
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    invocation = json.loads(arguments_path.read_text(encoding="utf-8"))
    assert invocation["args"] == ["--help"]
    assert invocation["callbackEndpoint"] is None
    assert get_status(environment).status == "stopped"


def test_codex_dynamic_information_command_ignores_invalid_codex_home(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Non-TUI forwarding does not inspect server-only configuration."""
    root, environment = server_environment
    original_home = environment["CODEX_HOME"]
    environment["CODEX_HOME"] = "/dev/null"
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
        "--version",
    ]

    try:
        result = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    finally:
        environment["CODEX_HOME"] = original_home

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "codex-cli 9.9.9"
    assert get_status(environment).status == "stopped"


@pytest.mark.parametrize(
    "arguments",
    [
        ["completion", "zsh"],
        ["--model", "o3", "exec", "hello"],
        ["-c", 'model="o3"', "review"],
        ["features", "list"],
        ["help", "mcp"],
        ["login", "status"],
        ["mcp", "list"],
        ["doctor"],
    ],
)
def test_codex_dynamic_forwards_non_tui_commands_without_remote(
    server_environment: tuple[Path, dict[str, str]],
    arguments: list[str],
) -> None:
    """Non-interactive Codex commands bypass the remote TUI transport."""
    root, environment = server_environment
    arguments_path = root / "arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(arguments_path)
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
        *arguments,
    ]

    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    invocation = json.loads(arguments_path.read_text(encoding="utf-8"))
    assert invocation["args"] == arguments
    assert invocation["callbackEndpoint"] is None
    assert get_status(environment).status == "stopped"


def test_codex_dynamic_preserves_tty_and_exact_exit_status(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Process replacement leaves terminal ownership and status with Codex."""
    root, environment = server_environment
    arguments_path = root / "arguments.json"
    environment["FAKE_CODEX_ARGS"] = str(arguments_path)
    environment["FAKE_CODEX_EXIT"] = "17"
    command = [
        sys.executable,
        "-c",
        "from claude_code_tools.codex_server_cli import dynamic_main; dynamic_main()",
    ]
    pid, descriptor = pty.fork()
    if pid == 0:
        os.execve(sys.executable, command, environment)
    try:
        while True:
            try:
                if not os.read(descriptor, 4096):
                    break
            except OSError:
                break
    finally:
        os.close(descriptor)
    _waited_pid, wait_status = os.waitpid(pid, 0)

    assert os.WIFEXITED(wait_status)
    assert os.WEXITSTATUS(wait_status) == 17
    invocation = json.loads(arguments_path.read_text(encoding="utf-8"))
    assert invocation["stdinIsTty"] is True
    assert invocation["stdoutIsTty"] is True


def test_external_server_is_rejected_and_never_stopped(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """An uncertified listener remains outside helper control."""
    _root, environment = server_environment
    codex = environment["CCTOOLS_CODEX_BIN"]
    paths = _desired_paths(environment)
    assert paths.generation is not None
    environment[server_models.CODEX_SERVER_GENERATION_ENV] = paths.generation
    external = subprocess.Popen(
        [codex, "app-server", "--listen", paths.endpoint],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_socket(paths.socket_path)
        with pytest.raises(CodexServerError, match="version could not be verified"):
            ensure_server(environment)

        code, output = _invoke(["stop"], environment)
        assert code != 0
        assert "was not started by codex-server" in output
        assert external.poll() is None

        code, output = _invoke(["restart"], environment)
        assert code != 0
        assert "externally owned" in output
        assert external.poll() is None
    finally:
        external.terminate()
        external.wait(timeout=5)


def test_stale_wrong_identity_with_live_group_is_retained_without_signalling(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """PID reuse protection retains ambiguous group ownership without signals."""
    root, environment = server_environment
    paths = _desired_paths(environment)
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    state = {
        "version": 1,
        "pid": innocent.pid,
        "pgid": innocent.pid,
        "processStartedAt": "definitely not this process",
        "codexPath": environment["CCTOOLS_CODEX_BIN"],
        "codexVersion": "old",
        "launchedAt": "2026-01-01T00:00:00+00:00",
        "phase": "running",
    }
    paths.state_path.write_text(json.dumps(state), encoding="utf-8")

    try:
        with pytest.raises(CodexServerError, match="ownership state was retained"):
            ensure_server(environment)
        assert innocent.poll() is None
        assert paths.state_path.exists()
        assert not (root / "launches.txt").exists()
    finally:
        paths.state_path.unlink(missing_ok=True)
        innocent.terminate()
        innocent.wait(timeout=5)


def test_unreachable_owned_server_requires_explicit_restart(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """A live helper stays owned until the user explicitly restarts it."""
    root, environment = server_environment
    started = ensure_server(environment)
    _paths(environment).socket_path.unlink()

    degraded = get_status(environment)
    assert degraded.status == "degraded"
    assert degraded.ownership == "helper"

    with pytest.raises(CodexServerError, match="listener vanished"):
        ensure_server(environment)
    preserved = get_status(environment)
    assert preserved.status == "degraded"
    assert preserved.ownership == "helper"
    assert preserved.pid == started.pid
    assert preserved.detail is not None
    assert "codex-server restart" in preserved.detail
    launches = (root / "launches.txt").read_text(encoding="utf-8").splitlines()
    assert len(launches) == 1

    recovered = restart_server(environment, allow_disconnect=True)
    assert recovered.status == "running"
    assert recovered.pid != started.pid
    launches = (root / "launches.txt").read_text(encoding="utf-8").splitlines()
    assert len(launches) == 2


def test_startup_failure_reports_log_and_cleans_state(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """An early child exit is actionable and leaves no owned state."""
    _root, environment = server_environment
    environment["FAKE_CODEX_FAIL_START"] = "1"

    code, output = _invoke(["start"], environment)
    assert code != 0
    assert "startup" in output
    assert "intentional startup failure" in output
    assert not _paths(environment).state_path.exists()


def test_stale_socket_and_malformed_state_are_recovered(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Safe stale artifacts do not permanently block a new server."""
    _root, environment = server_environment
    paths = _desired_paths(environment)
    paths.socket_path.parent.mkdir(mode=0o700, parents=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(paths.socket_path))
    stale.close()
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    paths.state_path.write_text("{broken", encoding="utf-8")

    status = ensure_server(environment)
    assert status.status == "running"
    assert status.ownership == "helper"
    quarantined = list(paths.runtime_dir.glob("state.invalid.*.json"))
    assert len(quarantined) == 1


def test_non_socket_endpoint_entry_is_preserved(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """The helper refuses a regular file at Codex's socket path."""
    _root, environment = server_environment
    socket_path = _desired_paths(environment).socket_path
    socket_path.parent.mkdir(mode=0o700, parents=True)
    socket_path.write_text("keep me", encoding="utf-8")

    code, output = _invoke(["start"], environment)
    assert code != 0
    assert "non-socket entry" in output
    assert socket_path.read_text(encoding="utf-8") == "keep me"


def test_app_server_log_symlink_is_refused(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Starting the helper never follows or modifies an existing log symlink."""
    root, environment = server_environment
    paths = _desired_paths(environment)
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    target = root / "keep.txt"
    target.write_text("keep me", encoding="utf-8")
    paths.log_path.symlink_to(target)

    code, output = _invoke(["start"], environment)

    assert code != 0
    assert "app-server log" in output
    assert target.read_text(encoding="utf-8") == "keep me"
    assert not (root / "launches.txt").exists()


def test_logs_rejects_path_replacing_supervisor_owned_inode(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Readers never switch from the supervisor's open inode to a new path."""
    root, environment = server_environment
    ensure_server(environment)
    paths = _paths(environment)
    original_log = root / "supervisor-owned.log"
    paths.log_path.rename(original_log)
    paths.log_path.write_bytes(b"untrusted replacement\n")

    code, output = _invoke(["logs"], environment)

    assert code != 0
    assert "no longer names the supervisor-owned file" in output
    assert "untrusted replacement" not in output


def test_internal_log_tail_ignores_replacement_path(tmp_path: Path) -> None:
    """Startup diagnostics cannot read a path that replaced their open log."""
    path = tmp_path / "server.log"
    path.write_bytes(b"trusted original\n")
    info = path.stat()
    identity = (info.st_dev, info.st_ino)
    path.rename(tmp_path / "original.log")
    path.write_bytes(b"untrusted replacement\n")

    assert server_models.log_tail(path, expected_identity=identity) == ""


def test_state_and_runtime_permissions_are_private(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Lifecycle metadata and logs are private to the current user."""
    _root, environment = server_environment
    ensure_server(environment)
    paths = _paths(environment)

    assert stat.S_IMODE(paths.runtime_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.lock_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.log_path.stat().st_mode) == 0o600
