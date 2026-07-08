"""The >trim hook flows, exercised via a REAL subprocess (no mocks).

The UserPromptSubmit hook shells out to the `aichat trim-in-place` CLI;
these tests swap that CLI for a stub executable (``AICHAT_BIN``) that
records its argv to a file and prints canned single-line JSON, so the
whole prompt -> preview -> apply/cancel pipeline runs end to end. The
pending-state directory is redirected via ``AICHAT_TRIM_STATE_DIR``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "aichat"
    / "hooks"
    / "aichat_resume_hook.py"
)

SESSION_ID = "sess-trim-test"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Stub `aichat` CLI: appends its argv (JSON, one line per call) to a
# recording file and prints a canned single-line JSON result. Behavior
# is switched per-test via the STUB_MODE env var (inherited from the
# hook process): "" = trimmable preview/apply, "lean" = nothing to
# trim, "error" = {"error": ...}, "badjson" = garbage output,
# "empty" = bare {}, "wrongtype" = string-typed numeric fields,
# "nanresult" = NaN tokens_saved, "hugeint" = astronomically large
# integer sizes (float() would overflow), "negative" = negative
# savings, "floatnum" = float-typed tokens_saved, "nobackup" =
# applied without a backup_file, "noisy" = warning line before the
# JSON, "exitfail" = valid result JSON but nonzero exit.
STUB_SOURCE = '''#!/usr/bin/env python3
"""Stub `aichat` CLI for hook tests (records argv, prints JSON)."""
import json
import os
import sys

RECORD = __RECORD_PATH__

args = sys.argv[1:]
with open(RECORD, "a") as f:
    f.write(json.dumps(args) + "\\n")

mode = os.environ.get("STUB_MODE", "")
if mode == "error":
    print(json.dumps({"error": "boom: simulated failure"}))
    sys.exit(1)
if mode == "badjson":
    print("total garbage, not json at all")
    sys.exit(0)
if mode == "empty":
    print("{}")
    sys.exit(0)
if mode == "wrongtype":
    print(json.dumps({
        "tokens_saved": "many",
        "chars_saved": "lots",
        "nothing_to_trim": False,
    }))
    sys.exit(0)

result = {
    "applied": False,
    "dry_run": "--dry-run" in args,
    "nothing_to_trim": False,
    "num_tools_trimmed": 3,
    "num_assistant_trimmed": 1,
    "chars_saved": 20000,
    "tokens_saved": 5000,
    "backup_file": None,
    "session_file": args[1] if len(args) > 1 else "",
    "size_before": 100000,
    "size_after": 80000,
}
if mode == "lean":
    result["nothing_to_trim"] = True
    result["tokens_saved"] = 120
    result["chars_saved"] = 480
    result["num_tools_trimmed"] = 0
    result["num_assistant_trimmed"] = 0
    result["size_after"] = 100000
elif "--dry-run" not in args:
    result["applied"] = True
    result["backup_file"] = "/fake/x.bak"
if mode == "nanresult":
    result["tokens_saved"] = float("nan")
if mode == "hugeint":
    result["size_before"] = 10**400
    result["size_after"] = 10**400
if mode == "negative":
    result["tokens_saved"] = -5000
    result["chars_saved"] = -20000
if mode == "floatnum":
    result["tokens_saved"] = 5000.5
if mode == "nobackup":
    result["backup_file"] = None
if mode == "noisy":
    print("warning: something happened")
print(json.dumps(result))
if mode == "exitfail":
    sys.exit(3)
'''


class Harness:
    """Per-test wiring: stub aichat CLI, argv recording, state dir.

    Attributes:
        state_dir: Directory the hook stores pending-trim state in.
        record: File the stub appends each call's argv to.
        stub: Path of the stub `aichat` executable.
        transcript: A fake session transcript file (contents unused).
    """

    def __init__(self, tmp_path: Path) -> None:
        self.state_dir = tmp_path / "state"
        self.record = tmp_path / "calls.jsonl"
        self.stub = tmp_path / "aichat"
        self.stub.write_text(
            STUB_SOURCE.replace(
                "__RECORD_PATH__", json.dumps(str(self.record))
            )
        )
        self.stub.chmod(0o755)
        self.transcript = tmp_path / "current-session.jsonl"
        self.transcript.write_text('{"type": "user"}\n')

    def run(
        self,
        prompt: str,
        *,
        session_id: str = SESSION_ID,
        transcript: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke the hook with a payload; assert it exits 0.

        Args:
            prompt: The user prompt to submit.
            session_id: Payload session_id (empty string allowed).
            transcript: Payload transcript_path override; None means
                the harness's real transcript file.
            env: Extra env vars (override the defaults, e.g. STUB_MODE
                or a different AICHAT_BIN / PATH).

        Returns:
            The completed hook process.
        """
        payload = {
            "session_id": session_id,
            "prompt": prompt,
            "cwd": "/work",
            "transcript_path": (
                str(self.transcript) if transcript is None else transcript
            ),
        }
        full_env = {
            **os.environ,
            # Ensure `#!/usr/bin/env python3` in the stub resolves.
            "PATH": (
                os.path.dirname(sys.executable)
                + os.pathsep
                + os.environ.get("PATH", "")
            ),
            "AICHAT_TRIM_STATE_DIR": str(self.state_dir),
            "AICHAT_BIN": str(self.stub),
        }
        if env:
            full_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=full_env,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return proc

    def calls(self) -> list[list[str]]:
        """Return the argv of every stub invocation so far."""
        if not self.record.exists():
            return []
        return [
            json.loads(line)
            for line in self.record.read_text().splitlines()
            if line.strip()
        ]

    def state_file(self, session_id: str = SESSION_ID) -> Path:
        """Path of the pending-trim state file for a session."""
        return self.state_dir / f"trim-pending.{session_id}.json"


@pytest.fixture()
def hk(tmp_path: Path) -> Harness:
    """A fresh harness (stub CLI + state dir) per test."""
    return Harness(tmp_path)


def _reason(proc: subprocess.CompletedProcess[str]) -> str:
    """Parse the hook's block JSON and return its ANSI-stripped reason."""
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    return ANSI_RE.sub("", out["reason"])


# ---------------------------------------------------------------------------
# Trigger matching / pass-through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    ["please trim the hedges", ">trimming plans", ">trims"],
)
def test_non_trigger_prompts_pass_through(hk: Harness, prompt: str) -> None:
    proc = hk.run(prompt)
    assert proc.stdout.strip() == ""  # no block JSON at all
    assert hk.calls() == []
    assert not hk.state_file().exists()


def test_resume_trigger_still_blocks(hk: Harness, tmp_path: Path) -> None:
    # Regression: the trim refactor must not break '>resume'. PATH is
    # pointed at an empty dir so no clipboard tool is found (keeps the
    # developer's real clipboard untouched) -> the "could not copy"
    # variant, which still mentions the clipboard and the session ID.
    nobin = tmp_path / "nobin"
    nobin.mkdir()
    proc = hk.run(">resume", env={"PATH": str(nobin)})
    reason = _reason(proc)
    assert "clipboard" in reason.lower()
    assert SESSION_ID in reason
    assert "aichat resume" in reason  # full resume instructions
    assert hk.calls() == []  # trim CLI not involved


def test_session_trigger_still_blocks(hk: Harness, tmp_path: Path) -> None:
    nobin = tmp_path / "nobin"
    nobin.mkdir()
    proc = hk.run(">session", env={"PATH": str(nobin)})
    reason = _reason(proc)
    assert "clipboard" in reason.lower()
    assert SESSION_ID in reason
    assert hk.calls() == []


def test_uppercase_trigger_is_case_insensitive(hk: Harness) -> None:
    proc = hk.run(">TRIM -5")
    reason = _reason(proc)
    assert "keep last 5 long assistant msgs" in reason
    calls = hk.calls()
    assert len(calls) == 1
    assert "--trim-assistant" in calls[0]
    idx = calls[0].index("--trim-assistant")
    assert calls[0][idx + 1] == "-5"
    assert "--dry-run" in calls[0]


# ---------------------------------------------------------------------------
# Preview (dry-run) flows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arg", ["help", "-h", "--help", "?", "HELP"])
def test_trim_help_is_pure_text(hk: Harness, arg: str) -> None:
    # '>trim help' explains the flow WITHOUT reading the transcript,
    # calling the CLI, or arming any pending state.
    proc = hk.run(f">trim {arg}")
    reason = _reason(proc)
    assert ">trim yes" in reason  # explains the apply step
    assert ">trim -20" in reason  # shows the options
    assert hk.calls() == []  # no CLI invocation
    assert not hk.state_file().exists()  # nothing armed


def test_trim_help_works_without_transcript_path(hk: Harness) -> None:
    # Help must render even before a transcript path exists.
    proc = hk.run(">trim help", transcript="")
    reason = _reason(proc)
    assert ">trim yes" in reason
    assert hk.calls() == []


def test_bare_trim_previews_and_arms_state(hk: Harness) -> None:
    proc = hk.run(">trim")
    reason = _reason(proc)
    assert "5,000" in reason  # tokens from the stub preview
    assert ">trim yes" in reason
    assert ">trim cancel" in reason
    assert ">trim -20" in reason  # usage help shown on bare '>trim'

    assert hk.calls() == [
        [
            "trim-in-place",
            str(hk.transcript),
            "--json",
            "--len",
            "500",
            "--dry-run",
        ]
    ]

    state = hk.state_file()
    assert state.exists()
    plan = json.loads(state.read_text())
    assert plan["opts"] == {
        "threshold": None,
        "trim_assistant": None,
        "tools": [],
    }
    assert plan["transcript_path"] == str(hk.transcript)
    assert plan["preview_tokens"] == 5000
    assert plan["created_at"] == pytest.approx(time.time(), abs=120)


def test_trim_args_forwarded_to_cli(hk: Harness) -> None:
    proc = hk.run(">trim -20 800 bash,read")
    reason = _reason(proc)
    assert "keep last 20 long assistant msgs" in reason
    assert "threshold 800 chars" in reason
    assert "tools: bash,read" in reason
    assert hk.calls() == [
        [
            "trim-in-place",
            str(hk.transcript),
            "--json",
            "--len",
            "800",
            "--tools",
            "bash,read",
            "--trim-assistant",
            "-20",
            "--dry-run",
        ]
    ]
    assert hk.state_file().exists()


def test_word_tokens_accumulate_as_tools(hk: Harness) -> None:
    hk.run(">trim bash read")
    assert hk.calls() == [
        [
            "trim-in-place",
            str(hk.transcript),
            "--json",
            "--len",
            "500",
            "--tools",
            "bash,read",
            "--dry-run",
        ]
    ]


@pytest.mark.parametrize(
    ("bad", "phrase"),
    [
        ("--20", "Unrecognized token: '--20'."),
        ("0", "Threshold must be a positive number."),
        ("-0", "Assistant spec must be non-zero"),
        # Overlong numeric tokens must be a parse error, never an
        # int() crash (Python 3.11+ digit-limit ValueError) that
        # would silently pass the prompt through.
        ("9" * 5000, "Threshold is too large."),
        ("-" + "9" * 5000, "Assistant spec is too large."),
        ("+" + "9" * 5000, "Assistant spec is too large."),
        ("9" * 13, "Threshold is too large."),
        # Overlong tool names are rejected at parse time, matching
        # what pending-state validation will accept back.
        ("x" * 200, "Tool name is too long."),
    ],
)
def test_invalid_tokens_show_usage_without_arming(
    hk: Harness, bad: str, phrase: str
) -> None:
    proc = hk.run(f">trim {bad}")
    reason = _reason(proc)
    assert phrase in reason
    assert ">trim -20" in reason  # usage lines included
    assert hk.calls() == []  # CLI never invoked
    assert not hk.state_file().exists()


@pytest.mark.parametrize(
    ("arg", "phrase"),
    [
        ("-5 -10", "More than one assistant spec ('-10')."),
        ("500 800", "More than one threshold ('800')."),
    ],
)
def test_duplicate_specs_rejected(
    hk: Harness, arg: str, phrase: str
) -> None:
    proc = hk.run(f">trim {arg}")
    reason = _reason(proc)
    assert phrase in reason
    assert hk.calls() == []
    assert not hk.state_file().exists()


@pytest.mark.parametrize("arg", ["bash,", ",bash", "bash,,read"])
def test_empty_tool_segments_rejected(hk: Harness, arg: str) -> None:
    """A trailing/leading/doubled comma is malformed input, not a tool
    list: usage is shown, the CLI never runs, no state is armed."""
    proc = hk.run(f">trim {arg}")
    reason = _reason(proc)
    assert f"Unrecognized token: '{arg}'." in reason
    assert ">trim -20" in reason  # usage lines included
    assert hk.calls() == []
    assert not hk.state_file().exists()


def test_lean_preview_does_not_arm_state(hk: Harness) -> None:
    proc = hk.run(">trim", env={"STUB_MODE": "lean"})
    reason = _reason(proc)
    assert "already lean" in reason
    assert not hk.state_file().exists()
    calls = hk.calls()
    assert len(calls) == 1 and "--dry-run" in calls[0]


# ---------------------------------------------------------------------------
# Apply / cancel / pending-state lifecycle
# ---------------------------------------------------------------------------


def test_yes_without_pending_state(hk: Harness) -> None:
    proc = hk.run(">trim yes")
    reason = _reason(proc)
    assert "No pending trim preview" in reason
    assert hk.calls() == []  # nothing applied blindly


def test_preview_then_yes_applies_same_opts(hk: Harness) -> None:
    hk.run(">trim -20 800 bash,read")
    proc = hk.run(">trim yes")
    reason = _reason(proc)
    assert "5,000 tokens saved" in reason
    assert "/fake/x.bak" in reason  # backup path reported

    calls = hk.calls()
    assert len(calls) == 2
    preview, apply = calls
    assert "--dry-run" in preview
    # The apply re-runs the CLI with the SAME opts, minus --dry-run.
    assert apply == [
        "trim-in-place",
        str(hk.transcript),
        "--json",
        "--len",
        "800",
        "--tools",
        "bash,read",
        "--trim-assistant",
        "-20",
    ]
    assert not hk.state_file().exists()  # consumed


def test_preview_then_cancel_clears_state(hk: Harness) -> None:
    hk.run(">trim")
    assert hk.state_file().exists()
    proc = hk.run(">trim cancel")
    reason = _reason(proc)
    assert "abandoned" in reason
    assert not hk.state_file().exists()
    assert len(hk.calls()) == 1  # only the preview ran


def test_expired_state_requires_fresh_preview(hk: Harness) -> None:
    hk.state_dir.mkdir(parents=True, exist_ok=True)
    hk.state_file().write_text(
        json.dumps(
            {
                "created_at": time.time() - 10_000,  # TTL is 600s
                "transcript_path": str(hk.transcript),
                "opts": {
                    "threshold": None,
                    "trim_assistant": None,
                    "tools": [],
                },
                "preview_tokens": 5000,
            }
        )
    )
    proc = hk.run(">trim yes")
    reason = _reason(proc)
    assert "No pending trim preview" in reason
    assert hk.calls() == []
    assert not hk.state_file().exists()  # expired state cleaned up


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_cli_error_json_reported_gracefully(hk: Harness) -> None:
    proc = hk.run(">trim", env={"STUB_MODE": "error"})
    reason = _reason(proc)
    assert "boom: simulated failure" in reason
    assert not hk.state_file().exists()


def test_cli_garbage_output_reported_gracefully(hk: Harness) -> None:
    proc = hk.run(">trim", env={"STUB_MODE": "badjson"})
    reason = _reason(proc)
    assert "failed" in reason
    assert "total garbage" in reason  # CLI output surfaced as detail
    assert not hk.state_file().exists()


@pytest.mark.parametrize(
    "mode",
    [
        "empty",
        "wrongtype",
        "nanresult",
        "hugeint",
        "negative",
        "floatnum",
        "noisy",
        "exitfail",
    ],
)
def test_invalid_cli_output_is_a_graceful_error(
    hk: Harness, mode: str
) -> None:
    """Hostile/broken CLI stdout must never be trusted as a result:
    a bare {}, string-typed numeric fields, NaN numbers, huge ints
    that would overflow float() in size formatting, negative savings,
    float-typed counts, extra lines around the JSON, or a nonzero
    exit with result-looking JSON all yield a graceful error and arm
    no state."""
    proc = hk.run(">trim", env={"STUB_MODE": mode})
    reason = _reason(proc)
    assert "failed" in reason
    assert not hk.state_file().exists()


@pytest.mark.parametrize(
    "mode", ["wrongtype", "hugeint", "negative", "nobackup"]
)
def test_invalid_cli_output_at_apply_time_is_graceful(
    hk: Harness, mode: str
) -> None:
    """Hostile CLI output at APPLY time must produce a graceful block
    message (a formatting crash - wrong types or a 10**400 byte size
    reaching float() - would silently pass the '>trim yes' prompt
    through to the model). "applied" results with negative savings
    or no backup_file are equally untrustworthy."""
    hk.run(">trim")
    assert hk.state_file().exists()

    proc = hk.run(">trim yes", env={"STUB_MODE": mode})

    reason = _reason(proc)
    assert "Trim failed" in reason
    assert not hk.state_file().exists()


def test_missing_cli_suggests_install(hk: Harness) -> None:
    proc = hk.run(">trim", env={"AICHAT_BIN": "/nonexistent/binary"})
    reason = _reason(proc)
    assert "uv tool install" in reason
    assert not hk.state_file().exists()


@pytest.mark.parametrize("kind", ["directory", "nonexecutable", "empty"])
def test_bad_aichat_bin_is_a_graceful_error(
    hk: Harness, tmp_path: Path, kind: str
) -> None:
    """AICHAT_BIN pointing at a directory, a non-executable file, or
    an empty string raises OSError (not FileNotFoundError) inside
    subprocess.run; the hook must still block with a graceful message
    instead of silently passing the prompt through."""
    if kind == "directory":
        bad = tmp_path / "bin-dir"
        bad.mkdir()
        bin_value = str(bad)
    elif kind == "nonexecutable":
        bad = tmp_path / "not-executable"
        bad.write_text("#!/bin/sh\necho hi\n")
        bad.chmod(0o644)
        bin_value = str(bad)
    else:
        bin_value = ""

    proc = hk.run(">trim", env={"AICHAT_BIN": bin_value})

    reason = _reason(proc)
    assert "uv tool install" in reason
    assert "Trim preview failed" in reason
    assert not hk.state_file().exists()


def test_empty_session_id_blocks_with_message(hk: Harness) -> None:
    proc = hk.run(">trim", session_id="")
    assert _reason(proc) == "No session ID available."
    assert hk.calls() == []


def test_empty_transcript_path_is_friendly(hk: Harness) -> None:
    proc = hk.run(">trim", transcript="")
    reason = _reason(proc)
    assert "No transcript path" in reason
    assert hk.calls() == []
    assert not hk.state_file().exists()


# ---------------------------------------------------------------------------
# Duplicate tool names must be rejected (never previewed, never armed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arg",
    [
        "bash bash",  # duplicate word tokens
        "bash,bash",  # duplicate within one comma group
        "bash read,bash",  # duplicate across word + comma tokens
        "Bash bash",  # case-insensitive duplicate
        "-20 bash,read bash",  # duplicate mixed with other specs
    ],
)
def test_duplicate_tool_names_rejected(hk: Harness, arg: str) -> None:
    proc = hk.run(f">trim {arg}")
    reason = _reason(proc)
    assert "Duplicate tool name ('bash')." in reason
    assert ">trim -20" in reason  # usage lines included
    assert hk.calls() == []  # CLI never invoked
    assert not hk.state_file().exists()  # state never armed


# ---------------------------------------------------------------------------
# Hostile stdin fields: session_id is used in a filename
# ---------------------------------------------------------------------------


def test_non_string_session_id_is_blocked_safely(hk: Harness) -> None:
    proc = hk.run(">trim", session_id=123)  # type: ignore[arg-type]
    assert _reason(proc) == "No session ID available."
    assert hk.calls() == []
    assert not hk.state_dir.exists()  # nothing written anywhere


@pytest.mark.parametrize(
    "evil",
    [
        "../../../etc/passwd",  # traversal
        "a/b",  # slash escapes the filename shape
        "..",  # pure dot component
        ".hidden",  # must start alphanumeric
        "x" * 300,  # absurd length
    ],
)
def test_unsafe_session_id_blocks_before_any_work(
    hk: Harness, evil: str
) -> None:
    for prompt in (">trim", ">trim yes"):
        proc = hk.run(prompt, session_id=evil)
        reason = _reason(proc)
        assert "invalid" in reason.lower()
    assert hk.calls() == []  # no expensive preview, no apply
    assert not hk.state_dir.exists()  # no state file created anywhere


def test_state_dir_and_file_have_private_perms(hk: Harness) -> None:
    hk.run(">trim")
    state = hk.state_file()
    assert state.exists()
    dir_mode = stat.S_IMODE(hk.state_dir.stat().st_mode)
    file_mode = stat.S_IMODE(state.stat().st_mode)
    assert dir_mode & 0o077 == 0, oct(dir_mode)
    assert file_mode & 0o077 == 0, oct(file_mode)


# ---------------------------------------------------------------------------
# Shared /tmp hygiene: a pre-existing state dir is hostile until proven
# otherwise (symlink / file / foreign owner / permissive modes)
# ---------------------------------------------------------------------------


def test_symlinked_state_dir_never_armed_or_trusted(
    hk: Harness, tmp_path: Path
) -> None:
    """A symlink planted at the state dir path is refused for both
    saving (nothing written through it) and loading (planted plans
    are never applied)."""
    target = tmp_path / "symlink-target"
    target.mkdir()
    hk.state_dir.symlink_to(target)

    proc = hk.run(">trim")
    reason = _reason(proc)
    assert "nothing was armed" in reason.lower()
    assert "not a real directory" in reason
    assert list(target.iterdir()) == []  # nothing written through it

    # Even a valid-looking plan planted behind the symlink is ignored.
    plan = {
        "created_at": time.time(),
        "transcript_path": str(hk.transcript),
        "opts": {"threshold": None, "trim_assistant": None, "tools": []},
        "preview_tokens": 5000,
    }
    (target / f"trim-pending.{SESSION_ID}.json").write_text(
        json.dumps(plan)
    )
    proc = hk.run(">trim yes")
    assert "No pending trim preview" in _reason(proc)
    assert len(hk.calls()) == 1  # only the first preview; never applied


def test_state_dir_thats_a_file_fails_gracefully(hk: Harness) -> None:
    """A regular file squatting on the state dir path yields a block
    message (never a crash/pass-through) and is left untouched."""
    hk.state_dir.write_text("i am not a directory")

    proc = hk.run(">trim")

    reason = _reason(proc)
    assert "nothing was armed" in reason.lower()
    assert "Cannot use trim state dir" in reason
    assert hk.state_dir.read_text() == "i am not a directory"


def test_permissive_state_dir_is_tightened_to_0700(hk: Harness) -> None:
    """An existing user-owned but world-accessible state dir is
    chmod'ed back to 0700 before any state is written."""
    hk.state_dir.mkdir(parents=True)
    hk.state_dir.chmod(0o777)

    hk.run(">trim")

    dir_mode = stat.S_IMODE(hk.state_dir.stat().st_mode)
    assert dir_mode == 0o700, oct(dir_mode)
    assert hk.state_file().exists()


# ---------------------------------------------------------------------------
# Corrupt-but-JSON pending state == no state
# ---------------------------------------------------------------------------


def _hostile_plans(transcript: str) -> Iterator[Any]:
    """Parseable-but-wrong pending states ('>trim yes' must not run)."""
    now = time.time()
    good_opts = {"threshold": None, "trim_assistant": None, "tools": []}
    yield {"created_at": now, "opts": {"tools": 123}}  # missing path
    yield {"created_at": now, "transcript_path": 5, "opts": good_opts}
    yield {"created_at": now, "transcript_path": "", "opts": good_opts}
    yield {"created_at": now, "transcript_path": transcript, "opts": None}
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": {"threshold": "800", "trim_assistant": None, "tools": []},
    }
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": {"threshold": 0, "trim_assistant": None, "tools": []},
    }
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": {"threshold": None, "trim_assistant": True, "tools": []},
    }
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": {"threshold": None, "trim_assistant": None, "tools": [1]},
    }
    # tools entries the parser could never have saved: empty strings,
    # embedded commas, duplicates, un-lowercased names, option-looking
    # strings, absurd lengths. Treating these as valid would hand
    # attacker-shaped argv straight to the CLI on '>trim yes'.
    for bad_tools in (
        [""],
        ["bash,read"],
        ["bash", "bash"],
        ["Bash"],
        ["-n"],
        ["--dry-run"],
        ["x" * 200],
        [" bash"],
    ):
        yield {
            "created_at": now,
            "transcript_path": transcript,
            "opts": {
                "threshold": None,
                "trim_assistant": None,
                "tools": bad_tools,
            },
        }
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": good_opts,
        "preview_tokens": "many",
    }
    yield {"created_at": True, "transcript_path": transcript,
           "opts": good_opts}
    # json.load happily parses NaN/Infinity literals; none of these
    # may survive the TTL check (time.time() - NaN > 600 is False).
    yield {"created_at": float("nan"), "transcript_path": transcript,
           "opts": good_opts}
    yield {"created_at": float("inf"), "transcript_path": transcript,
           "opts": good_opts}
    yield {"created_at": float("-inf"), "transcript_path": transcript,
           "opts": good_opts}
    # Far-future timestamp: would otherwise never expire.
    yield {"created_at": now + 10**6, "transcript_path": transcript,
           "opts": good_opts}
    # Huge int: float(created_at) would raise OverflowError.
    yield {"created_at": 10**400, "transcript_path": transcript,
           "opts": good_opts}
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": good_opts,
        "preview_tokens": float("nan"),
    }
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": good_opts,
        "preview_tokens": float("inf"),
    }
    # trim_assistant 0 is a shape the parser rejects, so a saved plan
    # can never contain it.
    yield {
        "created_at": now,
        "transcript_path": transcript,
        "opts": {"threshold": None, "trim_assistant": 0, "tools": []},
    }
    yield [1, 2, 3]  # not even a dict


def test_corrupt_state_schema_is_treated_as_no_state(hk: Harness) -> None:
    hk.state_dir.mkdir(parents=True, exist_ok=True)
    for plan in _hostile_plans(str(hk.transcript)):
        hk.state_file().write_text(json.dumps(plan))

        proc = hk.run(">trim yes")

        reason = _reason(proc)
        assert "No pending trim preview" in reason, plan
        assert hk.calls() == [], plan  # never reaches the CLI
        assert not hk.state_file().exists(), plan  # corrupt file removed


# ---------------------------------------------------------------------------
# '>trim yes' applies the transcript path saved in the preview plan
# ---------------------------------------------------------------------------


def test_yes_with_changed_transcript_path_is_rejected(
    hk: Harness, tmp_path: Path
) -> None:
    hk.run(">trim")
    other = tmp_path / "other-session.jsonl"
    other.write_text('{"type": "user"}\n')

    proc = hk.run(">trim yes", transcript=str(other))

    reason = _reason(proc)
    assert "transcript path changed" in reason
    assert ">trim" in reason  # points at a fresh preview
    assert len(hk.calls()) == 1  # only the preview; nothing applied
    assert not hk.state_file().exists()  # stale plan cleared


def test_yes_with_empty_transcript_applies_stored_plan_path(
    hk: Harness,
) -> None:
    hk.run(">trim")

    proc = hk.run(">trim yes", transcript="")

    reason = _reason(proc)
    assert "tokens saved" in reason
    calls = hk.calls()
    assert len(calls) == 2
    # The apply targeted the PREVIEWED transcript, not the payload's.
    assert calls[1][1] == str(hk.transcript)
    assert "--dry-run" not in calls[1]


# ---------------------------------------------------------------------------
# The hook file must stay importable on any system Python >= 3.9
# ---------------------------------------------------------------------------


def _annotation_nodes(tree: ast.AST) -> Iterator[ast.expr]:
    """Yield every annotation expression in the module."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            all_args = (
                list(args.posonlyargs)
                + list(args.args)
                + list(args.kwonlyargs)
                + ([args.vararg] if args.vararg else [])
                + ([args.kwarg] if args.kwarg else [])
            )
            for arg in all_args:
                if arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def test_hook_source_is_python39_compatible() -> None:
    """No 3.10+ syntax: parses under the 3.9 grammar, no match
    statements, and no PEP 604 unions (``X | Y``) in annotations."""
    source = HOOK.read_text()

    # Best-effort grammar check against Python 3.9.
    ast.parse(source, feature_version=(3, 9))

    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Match) for node in ast.walk(tree)
    ), "match statements require Python 3.10+"

    for annotation in _annotation_nodes(tree):
        pep604 = [
            node
            for node in ast.walk(annotation)
            if isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitOr)
        ]
        assert pep604 == [], (
            f"PEP 604 union in annotation: {ast.dump(annotation)}"
        )
