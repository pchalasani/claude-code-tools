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

# The GitHub repo that carries both marketplaces, and the plugin name.
_MARKETPLACE_REPO = "pchalasani/claude-code-tools"
_PLUGIN = "voxtype"

# Claude and Codex use SEPARATE marketplaces in this repo, with different
# names and manifests: Claude reads .claude-plugin/marketplace.json
# (`cctools-plugins`); Codex reads .agents/plugins/marketplace.json
# (`cctools-codex-plugins`). Each agent also has its own install verb
# (`plugin install` vs `plugin add`), so the selector must name that
# agent's own marketplace or the install can't resolve the plugin.
# Each agent caches a marketplace snapshot, so an already-added
# marketplace is NOT re-fetched by `add` — without an explicit refresh a
# machine that added the marketplace before voxtype was published can't
# see the plugin. So we always refresh after add (Claude: `marketplace
# update`; Codex: `marketplace upgrade`) before installing.
_AGENTS: dict[str, dict[str, list[str]]] = {
    "claude": {
        "add": ["plugin", "marketplace", "add", _MARKETPLACE_REPO],
        "refresh": ["plugin", "marketplace", "update", "cctools-plugins"],
        "install": ["plugin", "install", f"{_PLUGIN}@cctools-plugins"],
    },
    "codex": {
        "add": ["plugin", "marketplace", "add", _MARKETPLACE_REPO],
        "refresh": ["plugin", "marketplace", "upgrade", "cctools-codex-plugins"],
        "install": ["plugin", "add", f"{_PLUGIN}@cctools-codex-plugins"],
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
    """Add + refresh the marketplace and install the plugin for ``agent``.

    The add and refresh steps are best-effort (add commonly "fails" only
    because the marketplace is already registered; refresh only matters
    when it was); success is decided by the install step, which is what
    actually places the skill.
    """
    verbs = _AGENTS[agent]
    print(f"voxtype: setting up the voxtype skill in {agent}…")
    add_rc = _run([agent, *verbs["add"]])
    if add_rc != 0:
        # Non-fatal: most often the marketplace is already registered.
        print(
            f"voxtype: '{agent} plugin marketplace add' returned {add_rc} "
            "(already added?); refreshing it"
        )
    # Refresh the cached marketplace snapshot so a machine that added it
    # before voxtype was published can see the plugin (non-fatal).
    _run([agent, *verbs["refresh"]])
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
            f"  claude plugin install {_PLUGIN}@cctools-plugins"
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
