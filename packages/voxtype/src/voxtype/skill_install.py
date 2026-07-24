"""`voxtype skill`: install the voxtype agent skill into Claude and Codex.

Both Claude Code and Codex read plugins from marketplaces. This adds the
``cctools-plugins`` marketplace (from the claude-code-tools GitHub repo)
and installs the ``voxtype`` plugin — which ships the ``voxtype-install``
guide skill — into whichever of the two agents is on the machine, so the
developer gets the skill in both with one command.
"""

from __future__ import annotations

import shutil
import subprocess

# The GitHub repo whose .claude-plugin/marketplace.json defines the
# `cctools-plugins` marketplace, and the plugin/marketplace to install.
_MARKETPLACE_REPO = "pchalasani/claude-code-tools"
_MARKETPLACE = "cctools-plugins"
_PLUGIN = "voxtype"

# Per-agent commands: (marketplace-add, plugin-install). Each agent uses
# its own CLI verbs but the same repo/plugin selector.
_AGENTS: dict[str, dict[str, list[str]]] = {
    "claude": {
        "add": ["plugin", "marketplace", "add", _MARKETPLACE_REPO],
        "install": ["plugin", "install", f"{_PLUGIN}@{_MARKETPLACE}"],
    },
    "codex": {
        "add": ["plugin", "marketplace", "add", _MARKETPLACE_REPO],
        "install": ["plugin", "add", f"{_PLUGIN}@{_MARKETPLACE}"],
    },
}


def _run(argv: list[str]) -> int:
    """Run a subprocess, streaming its output; return its exit code.

    Returns 127 if the executable is missing (it was probed with
    ``shutil.which`` first, so this is only a race/edge guard).
    """
    try:
        return subprocess.run(argv).returncode
    except FileNotFoundError:
        return 127


def _install_for(agent: str) -> bool:
    """Add the marketplace and install the voxtype plugin for ``agent``.

    The marketplace-add is best-effort (it commonly "fails" only because
    the marketplace is already added); success is decided by the install
    step, which is what actually places the skill.
    """
    verbs = _AGENTS[agent]
    print(f"voxtype: setting up the voxtype skill in {agent}…")
    add_rc = _run([agent, *verbs["add"]])
    if add_rc != 0:
        # Non-fatal: most often the marketplace is already registered.
        print(
            f"voxtype: '{agent} plugin marketplace add' returned {add_rc} "
            "(already added?); continuing to install"
        )
    install_rc = _run([agent, *verbs["install"]])
    if install_rc == 0:
        print(f"voxtype: installed the voxtype skill for {agent} ✓")
        return True
    print(
        f"voxtype: could not install the voxtype plugin for {agent} "
        f"(exit {install_rc})"
    )
    return False


def install_skill() -> int:
    """Install the voxtype skill into every agent found on PATH.

    Returns:
        0 if at least one agent got the skill; 1 if none is installed or
        every attempt failed.
    """
    present = [a for a in _AGENTS if shutil.which(a) is not None]
    if not present:
        print(
            "voxtype: neither 'claude' (Claude Code) nor 'codex' found on "
            "PATH.\nInstall one of them first, then re-run 'voxtype skill'.\n"
            "Or add it manually:\n"
            f"  claude plugin marketplace add {_MARKETPLACE_REPO}\n"
            f"  claude plugin install {_PLUGIN}@{_MARKETPLACE}"
        )
        return 1
    for agent in _AGENTS:
        if shutil.which(agent) is None:
            print(f"voxtype: {agent} not found on PATH — skipping")
    results = [_install_for(agent) for agent in present]
    if any(results):
        print(
            "\nvoxtype: done. Ask your agent \"help me install voxtype\" to "
            "use the skill."
        )
        return 0
    return 1
