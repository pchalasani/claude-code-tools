#!/usr/bin/env python3
"""
Regression tests for case-insensitive tool_name dispatch (issue #186).

Every safety-hook entrypoint gates on `tool_name` before running any
check. When that gate compared exact case, a client spelling the tool
name differently ("bash" instead of "Bash") fell through to the
approve/allow branch -- the hook failed OPEN and looked healthy from
the outside.

These tests assert, for each entrypoint, that:
    - a lowercase/mixed-case spelling still reaches the safety check
    - the canonical spelling still behaves exactly as before
    - an unrelated tool name is still passed through untouched
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from file_length_limit_hook import MAX_FILE_LINES, check_file_length_limit

HOOKS_DIR = Path(__file__).resolve().parent

# Spellings a client might send for a single tool.
BASH_SPELLINGS = ("Bash", "bash", "BASH", "BaSh")

# Standalone Bash entrypoints, each with a command its own check blocks.
BASH_ENTRYPOINTS = (
    ("bash_hook.py", "rm secrets.txt"),
    ("rm_block_hook.py", "rm secrets.txt"),
    ("env_file_protection_hook.py", "cat .env"),
    ("git_add_block_hook.py", "git add -A"),
    ("git_checkout_safety_hook.py", "git checkout -f"),
    ("git_commit_block_hook.py", "git commit -m wip"),
)


def run_hook(script: str, payload: dict, cwd: str) -> dict:
    """Run a hook entrypoint on a JSON payload and return its response.

    Args:
        script: Filename of the entrypoint inside the hooks directory.
        payload: Hook input, serialized to the script's stdin.
        cwd: Working directory for the subprocess.

    Returns:
        The parsed JSON object the entrypoint printed on stdout.
    """
    env = {**os.environ, "HOME": cwd}
    # The commit hook has an escape hatch; keep it out of these tests so
    # a developer's shell cannot mask a real regression.
    env.pop("CCTOOLS_ALLOW_GIT", None)
    # bash_hook.py resolves its sibling modules through CLAUDE_PLUGIN_ROOT
    # when that is set. Drop it so these tests always exercise the hooks
    # checked out next to this file, not an installed copy of the plugin.
    env.pop("CLAUDE_PLUGIN_ROOT", None)

    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        env=env,
    )
    return json.loads(result.stdout)


def is_pass_through(response: dict) -> bool:
    """Return whether a hook response lets the tool call proceed unchecked."""
    if response.get("decision") == "approve":
        return True
    hook_output = response.get("hookSpecificOutput", {})
    return hook_output.get("permissionDecision") == "allow"


class TestBashEntrypointsFoldCase(unittest.TestCase):
    """Bash-gated entrypoints must not fail open on a case mismatch."""

    def test_every_spelling_reaches_the_safety_check(self) -> None:
        """No spelling of "Bash" lets a blocked command through."""
        for script, command in BASH_ENTRYPOINTS:
            for spelling in BASH_SPELLINGS:
                with self.subTest(script=script, tool_name=spelling):
                    with tempfile.TemporaryDirectory() as work_dir:
                        response = run_hook(
                            script,
                            {
                                "tool_name": spelling,
                                # Unique, so no allow-flag file exists.
                                "session_id": f"case-test-{os.getpid()}",
                                "tool_input": {"command": command},
                            },
                            work_dir,
                        )
                    self.assertFalse(
                        is_pass_through(response),
                        f"{script} failed open for tool_name={spelling!r}: "
                        f"{response}",
                    )

    def test_unrelated_tool_name_still_passes_through(self) -> None:
        """Folding case must not widen the gate to other tools."""
        for script, command in BASH_ENTRYPOINTS:
            with self.subTest(script=script):
                with tempfile.TemporaryDirectory() as work_dir:
                    response = run_hook(
                        script,
                        {
                            "tool_name": "Grep",
                            "tool_input": {"command": command},
                        },
                        work_dir,
                    )
                self.assertTrue(
                    is_pass_through(response),
                    f"{script} intercepted an unrelated tool: {response}",
                )

    def test_missing_tool_name_still_passes_through(self) -> None:
        """A payload with no tool_name must not crash the entrypoint."""
        for script, command in BASH_ENTRYPOINTS:
            with self.subTest(script=script):
                with tempfile.TemporaryDirectory() as work_dir:
                    response = run_hook(
                        script,
                        {"tool_input": {"command": command}},
                        work_dir,
                    )
                self.assertTrue(
                    is_pass_through(response),
                    f"{script} intercepted a payload with no tool_name: "
                    f"{response}",
                )


class TestReadEntrypointFoldsCase(unittest.TestCase):
    """The Read hook must protect dotenv files regardless of spelling."""

    def test_every_spelling_blocks_dotenv_reads(self) -> None:
        """"Read", "read" and "READ" all deny a .env read."""
        for spelling in ("Read", "read", "READ", "ReAd"):
            with self.subTest(tool_name=spelling):
                with tempfile.TemporaryDirectory() as work_dir:
                    response = run_hook(
                        "read_env_protection_hook.py",
                        {
                            "tool_name": spelling,
                            "tool_input": {"file_path": ".env"},
                        },
                        work_dir,
                    )
                self.assertEqual(
                    response["hookSpecificOutput"]["permissionDecision"],
                    "deny",
                    f"failed open for tool_name={spelling!r}: {response}",
                )

    def test_unrelated_tool_name_still_passes_through(self) -> None:
        """A non-Read tool is still allowed through untouched."""
        with tempfile.TemporaryDirectory() as work_dir:
            response = run_hook(
                "read_env_protection_hook.py",
                {"tool_name": "Glob", "tool_input": {"file_path": ".env"}},
                work_dir,
            )
        self.assertTrue(is_pass_through(response))


class TestFileLengthHookFoldsCase(unittest.TestCase):
    """The file-length hook must dispatch Write and Edit case-insensitively."""

    def _oversized(self) -> str:
        """Return file content that exceeds the line limit."""
        return "x = 1\n" * (MAX_FILE_LINES + 1)

    def _check_in_temp_dir(self, payload: dict) -> bool:
        """Run check_file_length_limit from a scratch directory.

        The check drops a `.claude_file_length_warning.flag` speed-bump
        file in the working directory, so each call needs a fresh one.
        """
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as work_dir:
            os.chdir(work_dir)
            try:
                if payload["tool_name"].lower() == "edit":
                    Path("oversized.py").write_text(self._oversized())
                should_block, _ = check_file_length_limit(payload)
            finally:
                os.chdir(original_cwd)
        return should_block

    def test_write_is_checked_for_every_spelling(self) -> None:
        """Every spelling of "Write" is length-checked."""
        for spelling in ("Write", "write", "WRITE", "WrItE"):
            with self.subTest(tool_name=spelling):
                blocked = self._check_in_temp_dir({
                    "tool_name": spelling,
                    "tool_input": {
                        "file_path": "oversized.py",
                        "content": self._oversized(),
                    },
                })
                self.assertTrue(blocked, f"failed open for {spelling!r}")

    def test_edit_is_checked_for_every_spelling(self) -> None:
        """Every spelling of "Edit" is length-checked.

        This also covers `get_resulting_line_count`, which dispatches on
        the tool name a second time to decide how to compute the result.
        """
        for spelling in ("Edit", "edit", "EDIT", "EdIt"):
            with self.subTest(tool_name=spelling):
                blocked = self._check_in_temp_dir({
                    "tool_name": spelling,
                    "tool_input": {
                        "file_path": "oversized.py",
                        "old_string": "x = 1",
                        "new_string": "y = 2",
                    },
                })
                self.assertTrue(blocked, f"failed open for {spelling!r}")

    def test_unrelated_tool_name_is_not_checked(self) -> None:
        """Folding case must not widen the gate to other tools."""
        blocked = self._check_in_temp_dir({
            "tool_name": "NotebookEdit",
            "tool_input": {
                "file_path": "oversized.py",
                "content": self._oversized(),
            },
        })
        self.assertFalse(blocked)

    def test_entrypoint_denies_lowercase_write(self) -> None:
        """End to end, the entrypoint denies a lowercase oversized Write."""
        with tempfile.TemporaryDirectory() as work_dir:
            response = run_hook(
                "file_length_limit_hook.py",
                {
                    "tool_name": "write",
                    "tool_input": {
                        "file_path": "oversized.py",
                        "content": self._oversized(),
                    },
                },
                work_dir,
            )
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"], "deny"
        )


class TestHooksJsonMatchers(unittest.TestCase):
    """The registered matchers must accept what the scripts accept."""

    # Matcher index in hooks.json PreToolUse -> tool name it gates.
    MATCHED_TOOLS = ("Bash", "Edit", "Write", "Read")

    def setUp(self) -> None:
        config = json.loads((HOOKS_DIR / "hooks.json").read_text())
        self.matchers = [
            entry["matcher"] for entry in config["hooks"]["PreToolUse"]
        ]

    def test_one_matcher_per_gated_tool(self) -> None:
        """hooks.json still registers exactly the four PreToolUse gates."""
        self.assertEqual(len(self.matchers), len(self.MATCHED_TOOLS))

    def test_matchers_accept_every_spelling(self) -> None:
        """Each matcher accepts its tool name in any case."""
        for matcher, tool in zip(self.matchers, self.MATCHED_TOOLS):
            for spelling in (tool, tool.lower(), tool.upper()):
                with self.subTest(matcher=matcher, spelling=spelling):
                    self.assertIsNotNone(
                        re.fullmatch(matcher, spelling),
                        f"{matcher!r} does not accept {spelling!r}",
                    )

    def test_matchers_reject_other_tools(self) -> None:
        """A matcher must not capture a different tool's calls."""
        for matcher, tool in zip(self.matchers, self.MATCHED_TOOLS):
            for other in ("BashOutput", "NotebookEdit", "WebFetch", "Grep"):
                with self.subTest(matcher=matcher, other=other):
                    self.assertIsNone(
                        re.fullmatch(matcher, other),
                        f"{matcher!r} for {tool} also matches {other!r}",
                    )


if __name__ == "__main__":
    unittest.main()
