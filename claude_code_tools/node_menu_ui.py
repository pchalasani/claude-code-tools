"""Bridge to Node-based session UI.

Provides a thin IPC wrapper that launches the Node menu renderer, passes
sessions/keywords, and dispatches the selected action back into Python via the
provided action_handler.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

SessionDict = Dict[str, Any]

#: Runtime npm packages ``menu.js`` imports directly. A checkout that is
#: missing any of them cannot render a menu, so we check before spawning Node
#: rather than letting Node print a raw ``ERR_MODULE_NOT_FOUND`` stack trace.
NODE_UI_REQUIRED_PACKAGES = (
    "chalk",
    "figures",
    "ink",
    "ink-select-input",
    "meow",
    "react",
)


def _node_ui_dir() -> Path:
    """Return the directory holding the bundled Node UI."""
    here = Path(__file__).resolve()
    return here.parent.parent / "node_ui"


def _node_script_path() -> Path:
    """Return path to the bundled Node UI script."""
    return _node_ui_dir() / "menu.js"


def _missing_node_ui_packages(node_ui_dir: Path | None = None) -> List[str]:
    """Return the required Node UI packages that are not installed.

    Args:
        node_ui_dir: Directory to inspect. Defaults to the bundled ``node_ui``.

    Returns:
        Names of missing packages, empty when every requirement is present.
    """
    root = node_ui_dir if node_ui_dir is not None else _node_ui_dir()
    node_modules = root / "node_modules"
    if not node_modules.is_dir():
        return list(NODE_UI_REQUIRED_PACKAGES)
    return [
        name
        for name in NODE_UI_REQUIRED_PACKAGES
        if not (node_modules / name).is_dir()
    ]


def _quote_path(path: Path) -> str:
    """Quote a path for the shell the user is most likely pasting into.

    ``shlex.quote`` emits POSIX single quotes, which ``cmd.exe`` treats as
    literal characters, so a Windows path containing spaces would be split.

    Args:
        path: Path to embed in a suggested command line.

    Returns:
        The path, quoted only when it needs to be.
    """
    text = str(path)
    if os.name == "nt":
        # cmd.exe splits on & | ^ < > as well as spaces, so always quote.
        return f'"{text}"'
    return shlex.quote(text)


def _no_node_message() -> str:
    """Build the message shown when the Node runtime is unavailable."""
    return (
        "aichat could not start its interactive menu: 'node' was not found "
        "on PATH.\nInstall Node.js (>=18) and try again."
    )


def _node_ui_setup_message(missing: List[str], node_ui_dir: Path) -> str:
    """Build an actionable message for a Node UI with missing dependencies."""
    return (
        "aichat could not start its interactive menu: the Node UI "
        f"dependencies are missing ({', '.join(missing)}).\n"
        f"Expected them in: {node_ui_dir / 'node_modules'}\n"
        "\n"
        "This happens when aichat runs from a source checkout (an editable\n"
        "install) whose Node dependencies were never installed. Install them\n"
        "with either of:\n"
        f"  npm ci --prefix {_quote_path(node_ui_dir)} --omit=dev\n"
        "  make install   # from that checkout (needs GNU make)\n"
    )


def _write_payload(
    sessions: Iterable[SessionDict],
    keywords: List[str],
    focus_id: str | None = None,
    start_action: bool = False,
    start_screen: str | None = None,
    rpc_path: str | None = None,
    scope_line: str | None = None,
    tip_line: str | None = None,
    select_target: str | None = None,
    results_title: str | None = None,
    start_zoomed: bool = False,
    lineage_back_target: str | None = None,
    direct_action: str | None = None,
) -> Path:
    """Write payload to a temp file and return its path."""
    payload = {
        "sessions": list(sessions),
        "keywords": keywords,
        "focus_id": focus_id,
        "start_action": start_action,
        "start_screen": start_screen,
        "rpc_path": rpc_path,
        "scope_line": scope_line,
        "tip_line": tip_line,
        "select_target": select_target,
        "results_title": results_title,
        "start_zoomed": start_zoomed,
        "lineage_back_target": lineage_back_target,
        "direct_action": direct_action,
    }
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="-node-ui.json")
    Path(tmp.name).write_text(json.dumps(payload), encoding="utf-8")
    return Path(tmp.name)


def _run_node(data_path: Path, out_path: Path, stderr_mode: bool = False) -> int:
    """Invoke the Node UI process.

    Returns the process return code. A missing Node runtime or missing Node UI
    dependencies are reported as an actionable message and return code 1.
    """
    # Check the runtime before its packages: on a machine without Node, telling
    # the user to run npm is useless advice.
    if shutil.which("node") is None:
        print(_no_node_message(), file=sys.stderr)
        return 1

    node_ui_dir = _node_ui_dir()
    missing = _missing_node_ui_packages(node_ui_dir)
    if missing:
        print(_node_ui_setup_message(missing, node_ui_dir), file=sys.stderr)
        return 1

    script = _node_script_path()
    cmd = ["node", str(script), "--data", str(data_path), "--out", str(out_path)]
    env = os.environ.copy()
    if stderr_mode:
        env["NODE_UI_STDERR"] = "1"

    try:
        proc = subprocess.run(cmd, env=env)
    except FileNotFoundError:
        # Node disappeared between the check above and the spawn.
        print(_no_node_message(), file=sys.stderr)
        return 1
    return proc.returncode


def _read_result(out_path: Path) -> Dict[str, Any]:
    """Read the result file if it exists, else return empty dict."""
    import time

    # Small retry loop in case file isn't fully synced yet
    for _ in range(3):
        if not out_path.exists():
            time.sleep(0.05)
            continue
        try:
            content = out_path.read_text(encoding="utf-8").strip()
            if content:
                return json.loads(content)
        except (json.JSONDecodeError, IOError):
            pass
        time.sleep(0.05)
    return {}


def _run_node_menu_once(
    sessions: List[SessionDict],
    keywords: List[str],
    action_handler: Callable[[SessionDict, str, Dict[str, Any]], Any],
    stderr_mode: bool,
    focus_session_id: str | None,
    start_action: bool,
    start_screen: str | None,
    rpc_path: str | None,
    scope_line: str | None,
    tip_line: str | None,
    select_target: str | None,
    results_title: str | None,
    start_zoomed: bool,
    lineage_back_target: str | None,
    direct_action: str | None,
) -> str | None:
    """Run Node UI once and return result signal.

    Returns:
        'back' - action_handler wants to go back to resume menu
        'back_to_options' - user wants to go back to options
        None - normal exit (action completed or user cancelled)
    """
    data_path = _write_payload(
        sessions,
        keywords,
        focus_id=focus_session_id,
        start_action=start_action,
        start_screen=start_screen,
        rpc_path=rpc_path,
        scope_line=scope_line,
        tip_line=tip_line,
        select_target=select_target,
        results_title=results_title,
        start_zoomed=start_zoomed,
        lineage_back_target=lineage_back_target,
        direct_action=direct_action,
    )
    out_fd, out_path = tempfile.mkstemp(suffix="-node-ui-out.json")
    os.close(out_fd)
    out_file = Path(out_path)

    try:
        code = _run_node(data_path, out_file, stderr_mode=stderr_mode)
        if code != 0:
            print("Node UI exited with code", code, file=sys.stderr)
            return None

        result = _read_result(out_file)
        session_id = result.get("session_id")
        action = result.get("action")
        kwargs = result.get("kwargs", {})

        if action == "back_to_options":
            return "back_to_options"

        if not session_id or not action:
            if result:
                print(f"Error: Missing session_id or action in result: {result}")
            return None

        session = next((s for s in sessions if s.get("session_id") == session_id), None)
        if not session:
            print(f"Error: Session {session_id} not found in {len(sessions)} sessions")
            return None

        handler_result = action_handler(session, action, kwargs)
        return 'back' if handler_result == 'back' else None
    finally:
        try:
            data_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            out_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_node_menu_ui(
    sessions: List[SessionDict],
    keywords: List[str],
    action_handler: Callable[[SessionDict, str, Dict[str, Any]], Any],
    stderr_mode: bool = False,
    focus_session_id: str | None = None,
    start_action: bool = False,
    start_screen: str | None = None,
    rpc_path: str | None = None,
    scope_line: str | None = None,
    tip_line: str | None = None,
    select_target: str | None = None,
    results_title: str | None = None,
    start_zoomed: bool = False,
    lineage_back_target: str | None = None,
    direct_action: str | None = None,
    exit_on_back: bool = False,
) -> str | None:
    """Launch Node UI and dispatch selected action.

    Handles 'back to resume' internally - if action_handler returns 'back',
    automatically re-shows the resume menu (unless exit_on_back=True).

    Args:
        exit_on_back: If True, return 'back' to caller instead of looping to
            resume menu. Use this when invoking from Rust search where we want
            to pop back to search results on cancel.

    Returns:
        "back_to_options" if user wants to go back to options menu.
        "back" if exit_on_back=True and action was cancelled.
        None otherwise.
    """
    current_screen = start_screen
    current_direct_action = direct_action
    current_start_action = start_action

    while True:
        result = _run_node_menu_once(
            sessions, keywords, action_handler, stderr_mode, focus_session_id,
            current_start_action, current_screen, rpc_path, scope_line, tip_line,
            select_target, results_title, start_zoomed, lineage_back_target,
            current_direct_action,
        )

        if result == 'back':
            if exit_on_back:
                # Return to caller (for Rust search pop-back)
                return 'back'
            # Go back to resume menu
            current_screen = 'resume'
            current_direct_action = None
            current_start_action = False  # Don't auto-start action on loop back
            continue

        # 'back_to_options' or None - return to caller
        return result


def run_find_options_ui(
    initial_options: Dict[str, Any],
    variant: str = "find",
) -> Dict[str, Any] | None:
    """
    Launch Node UI to interactively configure find options.

    Args:
        initial_options: Dict with initial option values (keywords, global, etc.)
        variant: One of 'find', 'find-claude', 'find-codex'

    Returns:
        Dict with user-selected options, or None if cancelled
    """
    payload = {
        "sessions": [],
        "keywords": [],
        "start_screen": "find_options",
        "find_options": initial_options,
        "find_variant": variant,
    }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="-find-opts.json")
    Path(tmp.name).write_text(json.dumps(payload), encoding="utf-8")
    data_path = Path(tmp.name)

    out_fd, out_path = tempfile.mkstemp(suffix="-find-opts-out.json")
    os.close(out_fd)
    out_file = Path(out_path)

    try:
        code = _run_node(data_path, out_file)
        if code != 0:
            return None

        result = _read_result(out_file)
        return result.get("find_options")
    finally:
        try:
            data_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            out_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_trim_confirm_ui(
    new_session_id: str | None = None,
    lines_trimmed: int = 0,
    tokens_saved: int = 0,
    output_file: str = "",
    nothing_to_trim: bool = False,
    original_session_id: str | None = None,
) -> str | None:
    """
    Launch Node UI to confirm trim action.

    Shows a confirmation dialog after a trim operation. Can handle two cases:
    1. Trim created a new file - shows Resume/Delete options
    2. Nothing to trim - shows Resume original/Back options

    Args:
        new_session_id: The newly created session ID (None if nothing_to_trim)
        lines_trimmed: Number of lines that were trimmed
        tokens_saved: Estimated tokens saved
        output_file: Path to the new session file
        nothing_to_trim: If True, show "nothing to trim" UI variant
        original_session_id: Original session ID (used when nothing_to_trim)

    Returns:
        'resume' - User wants to resume the session
        'delete' - User wants to delete the new file and exit
        'back' - User wants to go back to menu (nothing_to_trim case)
        'cancel' - User pressed Escape (keep file, don't resume)
        None - Error or unexpected result
    """
    payload = {
        "sessions": [],
        "keywords": [],
        "start_screen": "trim_confirm",
        "trim_info": {
            "new_session_id": new_session_id,
            "original_session_id": original_session_id,
            "lines_trimmed": lines_trimmed,
            "tokens_saved": tokens_saved,
            "output_file": output_file,
            "nothing_to_trim": nothing_to_trim,
        },
    }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="-trim-confirm.json")
    Path(tmp.name).write_text(json.dumps(payload), encoding="utf-8")
    data_path = Path(tmp.name)

    out_fd, out_path = tempfile.mkstemp(suffix="-trim-confirm-out.json")
    os.close(out_fd)
    out_file = Path(out_path)

    try:
        code = _run_node(data_path, out_file)
        if code != 0:
            return None

        result = _read_result(out_file)
        return result.get("trim_action")
    finally:
        try:
            data_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            out_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_dir_confirm_ui(
    current_dir: str,
    session_dir: str,
) -> str | None:
    """
    Launch Node UI to confirm directory change.

    Shows a confirmation dialog when a session is from a different directory.

    Args:
        current_dir: The current working directory
        session_dir: The session's project directory

    Returns:
        'yes' - User wants to change directory and proceed
        'no' - User wants to proceed without changing directory
        'cancel' - User pressed Escape (cancel action)
        None - Error or unexpected result
    """
    payload = {
        "sessions": [],
        "keywords": [],
        "start_screen": "dir_confirm",
        "dir_info": {
            "current_dir": current_dir,
            "session_dir": session_dir,
        },
    }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="-dir-confirm.json")
    Path(tmp.name).write_text(json.dumps(payload), encoding="utf-8")
    data_path = Path(tmp.name)

    out_fd, out_path = tempfile.mkstemp(suffix="-dir-confirm-out.json")
    os.close(out_fd)
    out_file = Path(out_path)

    try:
        code = _run_node(data_path, out_file)
        if code != 0:
            return None

        result = _read_result(out_file)
        return result.get("dir_choice")
    finally:
        try:
            data_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            out_file.unlink(missing_ok=True)
        except Exception:
            pass
