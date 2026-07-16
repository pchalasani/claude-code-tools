"""The `agent-tunnel share` command: publish a session from the terminal.

The out-of-session `>share`. Claude sessions are normally published by typing
``>share`` INSIDE the session (the plugin's UserPromptSubmit hook); Codex CLI
has no such hook, so this command is the way to share a codex session. Kept
out of cli.py to hold both files under the repo's file-length guideline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import click

from .codex_session import (
    codex_home_for,
    find_codex_session_file,
    find_latest_codex_session,
    rollout_cwd,
    rollout_session_id,
)
from .config import load_config
from .registry import Registry, sanitize_label
from .session import find_latest_session, transcript_dir
from .store import TunnelStore


def _claude_config_dir(transcript: str) -> str:
    """Claude config dir a transcript lives under (env fallback)."""
    if "/projects/" in transcript:
        return transcript.split("/projects/")[0]
    return os.environ.get("CLAUDE_CONFIG_DIR", "")


@click.command()
@click.argument("label", required=False)
@click.option("--config", type=click.Path(), help="Config TOML path.")
@click.option(
    "--agent",
    "agent_",
    type=click.Choice(["claude", "codex"]),
    default="claude",
    show_default=True,
    help="Agent CLI the session belongs to.",
)
@click.option(
    "--session", help="Session id to publish (default: newest in the project)."
)
@click.option(
    "--project",
    type=click.Path(),
    help="Project dir of that session (default: cwd).",
)
@click.option(
    "--write",
    is_flag=True,
    help=(
        "Let colleagues edit files. Claude: no shell. Codex: workspace-write "
        "also permits SANDBOXED commands (its sandbox confines by "
        "filesystem/network, not tool name)."
    ),
)
@click.option(
    "--dangerously-allow-bash",
    "allow_bash",
    is_flag=True,
    help="Also let colleagues run shell commands.",
)
@click.option(
    "--dangerously-skip-permissions",
    "skip_perms",
    is_flag=True,
    help="FULL access (needs allow_skip_permissions on the daemon).",
)
def share(
    label: Optional[str],
    config: Optional[str],
    agent_: str,
    session: Optional[str],
    project: Optional[str],
    write: bool,
    allow_bash: bool,
    skip_perms: bool,
) -> None:
    """Publish a session from the terminal, minting a colleague handle.

    The out-of-session `>share`: Claude sessions are normally published by
    typing >share INSIDE the session (the plugin hook); Codex CLI has no such
    hook, so this command is how you share a codex session:

    \b
      agent-tunnel share --agent codex my-handle

    Run it from the session's project dir (or pass --project); it publishes
    the NEWEST session recorded there unless --session is given. Without an
    access flag a re-share keeps the handle's previous access level.
    """
    try:
        cfg = load_config(path=Path(config) if config else None)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    store = TunnelStore(cfg.state_path)
    project_dir = Path(project or os.getcwd()).expanduser().resolve()
    clean_label = ""
    if label:
        clean_label = sanitize_label(label) or ""
        if not clean_label:
            raise click.ClickException(
                "Invalid handle. Use letters, digits, dashes (2-32 chars), "
                "e.g. payments-auth."
            )
    access = (
        "all"
        if skip_perms
        else "bash" if allow_bash else "write" if write else None
    )

    if agent_ == "codex":
        if session:
            tfile = find_codex_session_file(session)
            if tfile is None:
                raise click.ClickException(
                    f"No codex session {session!r} under ~/.codex/sessions."
                )
        else:
            tfile = find_latest_codex_session(
                project_dir, exclude=store.known_fork_ids()
            )
            if tfile is None:
                raise click.ClickException(
                    f"No Codex session found for {project_dir}. Pass "
                    "--session <id> or run from the session's project dir."
                )
        session_id = rollout_session_id(tfile)
        config_dir = str(codex_home_for(tfile) or "")
        transcript = str(tfile)
        cwd = rollout_cwd(tfile) or str(project_dir)
    else:
        if session:
            session_id = session
            tpath = transcript_dir(project_dir, cfg.claude_home) / (
                f"{session}.jsonl"
            )
            transcript = str(tpath) if tpath.exists() else ""
            config_dir = _claude_config_dir(transcript)
        else:
            latest = find_latest_session(
                project_dir,
                exclude=store.known_fork_ids(),
                claude_home=cfg.claude_home,
            )
            if latest is None:
                raise click.ClickException(
                    f"No Claude session found in {project_dir}. Pass "
                    "--session <id>, or type >share inside the session."
                )
            session_id = latest.stem
            transcript = str(latest)
            config_dir = _claude_config_dir(transcript)
        cwd = str(project_dir)

    handle, collision = Registry(cfg.registry_path).publish(
        session_id=session_id,
        cwd=cwd,
        config_dir=config_dir,
        agent=agent_,
        access=access,
        label=clean_label,
        transcript_path=transcript,
    )
    if collision or not handle:
        raise click.ClickException(
            f"Handle '{collision}' is already used by another session."
        )
    tag = f" [{agent_}]" if agent_ != "claude" else ""
    click.echo(f"Sharing session {session_id[:8]}…{tag} as: {handle}")
    if access in ("bash", "all"):
        click.echo(
            f"⚠️  {access.upper()} access — colleagues' agents can run "
            "commands via this handle. Trusted people only."
        )
    elif access == "write" and agent_ == "codex":
        # Unlike claude write (edits, never shell), codex workspace-write
        # can run SANDBOXED commands — surface that so "write" isn't read
        # as "no execution".
        click.echo(
            "⚠️  Codex WRITE = workspace-write: the fork can also run "
            "sandboxed commands (confined by filesystem/network, not tool "
            "name), not just edit files."
        )
    click.echo(
        f"Colleagues: post  {handle} <question>  in the agent-tunnel "
        f"channel. Revoke with: agent-tunnel revoke {handle}"
    )
