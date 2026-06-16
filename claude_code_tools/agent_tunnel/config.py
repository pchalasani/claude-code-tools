"""Configuration loading for agent-tunnel.

Config lives in TOML (default ~/.config/agent-tunnel/config.toml) and can be
overridden per-invocation via CLI options. `agent-tunnel init` writes a
commented sample file.

Sessions are NOT configured here — they are published at runtime from inside
each Claude session via the `>share` hook, which writes to the registry that
the daemon reads. This config only holds the Discord wiring and limits.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "agent-tunnel" / "config.toml"
DEFAULT_STATE_PATH = (
    Path.home() / ".local" / "state" / "agent-tunnel" / "state.json"
)
# Env-overridable so the >share hook and the daemon can share a path (and for
# tests). The hook honors the same AGENT_TUNNEL_REGISTRY variable.
DEFAULT_REGISTRY_PATH = Path(
    os.environ.get("AGENT_TUNNEL_REGISTRY")
    or (Path.home() / ".local" / "state" / "agent-tunnel" / "registry.json")
)

DEFAULT_PERSONA = (
    "You are answering a question relayed from a teammate over chat. "
    "They cannot see this terminal or your files, so answer in a "
    "self-contained way, formatted as chat-friendly markdown. "
    "Never reveal credentials, tokens, or the contents of .env files."
)

DEFAULT_ALLOWED_TOOLS = ["Read", "Grep", "Glob"]
# Names are validated by the claude CLI ("matches no known tool" is a hard
# error), so only currently existing tools may appear here.
DEFAULT_DISALLOWED_TOOLS = [
    "Write",
    "Edit",
    "NotebookEdit",
    "Bash",
    "Task",
    "Agent",
    "WebFetch",
    "WebSearch",
]
# "write" access also permits file edits, but never Bash/command execution.
WRITE_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit", "NotebookEdit"]
WRITE_DISALLOWED_TOOLS = ["Bash", "Task", "Agent", "WebFetch", "WebSearch"]
# "bash" access additionally permits command execution (>share
# --dangerously-allow-bash) so a fork can produce real PDFs/docx via pandoc &
# co. It is a strict escalation of "write": read < write < bash.
BASH_ALLOWED_TOOLS = WRITE_ALLOWED_TOOLS + ["Bash"]
BASH_DISALLOWED_TOOLS = ["Task", "Agent", "WebFetch", "WebSearch"]
# Tool presets keyed by the `access` level.
ACCESS_PRESETS = {
    "read": (DEFAULT_ALLOWED_TOOLS, DEFAULT_DISALLOWED_TOOLS),
    "write": (WRITE_ALLOWED_TOOLS, WRITE_DISALLOWED_TOOLS),
    "bash": (BASH_ALLOWED_TOOLS, BASH_DISALLOWED_TOOLS),
}


@dataclass
class DiscordConfig:
    """Discord-facing settings."""

    token_env: str = "AGENT_TUNNEL_DISCORD_TOKEN"
    # Optional file holding the bot token; used when the env var is unset, so
    # you can run `serve` without exporting anything each time.
    token_file: str = ""
    channel_ids: list[int] = field(default_factory=list)
    allowed_user_ids: list[int] = field(default_factory=list)
    allowed_role_ids: list[int] = field(default_factory=list)
    respond_to_dms: bool = False


@dataclass
class ClaudeConfig:
    """How forked Claude Code invocations are constructed."""

    binary: str = "claude"
    model: str = ""
    # Per-handle access ("read"/"write") is set at share time (>share --write).
    # Explicit tool lists here override that preset; empty = use the preset.
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "dontAsk"
    persona: str = DEFAULT_PERSONA
    headless_extra_args: list[str] = field(default_factory=list)
    tmux_extra_args: list[str] = field(default_factory=list)
    # Pre-trust a shared folder in ~/.claude.json before forking, so the
    # interactive (tmux) fork doesn't pop the trust dialog. Set false to
    # disable touching that config.
    auto_trust: bool = True
    # Override the config file holding trust state (default: ~/.claude.json,
    # honoring CLAUDE_CONFIG_DIR).
    trust_config_path: str = ""


def resolve_tools(
    claude: "ClaudeConfig", access: str = "read"
) -> tuple[list[str], list[str]]:
    """(allowed, disallowed) tools for a fork at the given per-handle access
    level ('read'/'write'); explicit config lists override the preset."""
    allowed_p, disallowed_p = ACCESS_PRESETS.get(access, ACCESS_PRESETS["read"])
    return (
        claude.allowed_tools or list(allowed_p),
        claude.disallowed_tools or list(disallowed_p),
    )


@dataclass
class LimitsConfig:
    """Throughput and safety limits."""

    max_concurrent: int = 2
    per_user_cooldown_s: float = 15.0
    answer_timeout_s: float = 600.0
    launch_timeout_s: float = 90.0
    # Backstop only: colleagues close threads with !done. Forks idle longer
    # than this are reaped so abandoned ones can't pile up. 0 disables it.
    pane_idle_ttl_min: float = 180.0
    max_inline_chars: int = 5500
    # Per-file size cap (MB) for both inbound uploads a colleague attaches and
    # outbound deliverables the bot posts back. 24 keeps us under Discord's
    # default 25 MB attachment limit on un-boosted servers.
    max_attachment_mb: float = 24.0
    # Most attachments accepted from a single colleague message.
    max_attachments: int = 10


@dataclass
class AttachmentsConfig:
    """Inbound-attachment handling (downloads + best-effort conversion)."""

    # Office files (.docx/.pptx/.xlsx/…) can't be opened by the Read tool, so
    # we best-effort convert them with whatever converter is on the host's
    # PATH. "auto" = use the best one found (LibreOffice→PDF, else pandoc→md,
    # else macOS textutil→txt); "off" = never convert. No converter is ever a
    # hard dependency — PDF/images/text always work without one.
    convert: str = "auto"
    # Advanced: a custom converter command overriding auto-detection. Tokens
    # {input} (the file) and {outdir} (where to drop the result) are
    # substituted; the new file appearing in {outdir} is taken as the output.
    convert_command: str = ""


@dataclass
class TunnelConfig:
    """Top-level agent-tunnel configuration."""

    backend: str = "headless"
    tmux_session: str = "agent-tunnel"
    state_path: Path = DEFAULT_STATE_PATH
    registry_path: Path = DEFAULT_REGISTRY_PATH
    claude_home: Optional[Path] = None
    # Only used by the `agent-tunnel ask` smoke test when no handle/session is
    # given (auto = newest session in this dir); never needed by `serve`.
    project_dir: Optional[Path] = None
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    attachments: AttachmentsConfig = field(default_factory=AttachmentsConfig)


def _apply(dc: Any, data: dict[str, Any]) -> None:
    """Copy known keys from a TOML table onto a dataclass instance."""
    for key, value in data.items():
        if hasattr(dc, key):
            setattr(dc, key, value)


def load_config(
    path: Optional[Path] = None,
    backend: Optional[str] = None,
    channel_ids: Optional[list[int]] = None,
    token_env: Optional[str] = None,
) -> TunnelConfig:
    """Load config from TOML, then apply CLI overrides.

    Args:
        path: Config file path; defaults to DEFAULT_CONFIG_PATH. A missing
            default file is fine (pure-CLI usage); an explicitly given but
            missing path is an error.
        backend: Override backend ("tmux" or "headless").
        channel_ids: Override watched Discord channel ids.
        token_env: Override env var name holding the Discord bot token.

    Returns:
        A fully populated TunnelConfig.

    Raises:
        FileNotFoundError: Explicit config path does not exist.
        ValueError: Unknown backend.
    """
    explicit = path is not None
    cfg_path = path or DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
    elif explicit:
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    cfg = TunnelConfig()
    tunnel_tbl = data.get("tunnel", {})
    for key in ("backend", "tmux_session"):
        if key in tunnel_tbl:
            setattr(cfg, key, tunnel_tbl[key])
    for key in ("state_path", "registry_path", "claude_home", "project_dir"):
        if key in tunnel_tbl:
            setattr(cfg, key, Path(tunnel_tbl[key]).expanduser())

    _apply(cfg.discord, data.get("discord", {}))
    _apply(cfg.claude, data.get("claude", {}))
    _apply(cfg.limits, data.get("limits", {}))
    _apply(cfg.attachments, data.get("attachments", {}))

    if backend:
        cfg.backend = backend
    if channel_ids:
        cfg.discord.channel_ids = list(channel_ids)
    if token_env:
        cfg.discord.token_env = token_env

    # Anchor configured paths to absolute. A relative path resolves against the
    # CONFIG FILE's directory (not the caller's CWD), so `serve` and CLI
    # commands launched from different directories with the same config agree
    # on the state/registry files. Absolute paths also fix inbound attachments:
    # the backend launches claude with cwd=rec.project_dir and exposes the
    # uploads dir via --add-dir, so a relative `uploads/...` would otherwise
    # resolve under the project (where Read can't open it).
    base = cfg_path.parent

    def _anchor(p: Path) -> Path:
        p = p.expanduser()
        return (p if p.is_absolute() else base / p).resolve()

    cfg.state_path = _anchor(cfg.state_path)
    cfg.registry_path = _anchor(cfg.registry_path)
    if cfg.claude_home is not None:
        cfg.claude_home = _anchor(cfg.claude_home)
    if cfg.project_dir is not None:
        cfg.project_dir = _anchor(cfg.project_dir)

    if cfg.backend not in ("tmux", "headless"):
        raise ValueError(f"Unknown backend: {cfg.backend!r}")
    return cfg


def sample_config() -> str:
    """Return a commented sample config file."""
    return f'''\
# agent-tunnel configuration
# See docs/agent-tunnel-spec.md in claude-code-tools for details.
#
# There is NO project/session setting here: you publish a session at runtime
# from inside it by typing  >share  (the hook mints a handle you give to
# colleagues). This file only configures Discord and limits.

[tunnel]
# Server mode (default "headless"):
#   "headless" = `claude -p` per question: clean JSON I/O, more reliable, no
#                tmux needed.       Launch:  agent-tunnel serve
#   "tmux"     = a real interactive claude per thread in a private tmux server
#                you can watch live. Launch: agent-tunnel serve --backend tmux
backend = "headless"
# Name of the dedicated tmux session holding fork windows (tmux mode only).
tmux_session = "agent-tunnel"

[discord]
# Env var that holds the bot token (never put the token itself here).
token_env = "AGENT_TUNNEL_DISCORD_TOKEN"
# Optional: file holding the token, used if the env var is unset — lets you
# run `serve` without exporting anything. (Path to a plain text file.)
# token_file = "~/Documents/tokens/discord-token.txt"
# Channel ids the bot watches (developer mode -> Copy Channel ID).
channel_ids = []
# Empty lists mean: anyone in the watched channels may ask.
allowed_user_ids = []
allowed_role_ids = []
respond_to_dms = false

[claude]
binary = "claude"
# Empty string = the published session's default model.
model = ""
# Remote turns are read-only by default. Grant access per session at share
# time:  >share --write <name>  (adds Write/Edit, never Bash), or
# >share --dangerously-allow-bash <name>  (also adds Bash/command execution,
# so a fork can build real PDFs/docx — only do this for trusted colleagues).
# Write/bash handles can hand deliverables back: a fork writes them into
# <project>/.agent-tunnel-out/ (git-ignored) and the bot posts them to chat.
# Advanced: explicit tool lists override the per-handle preset (empty = preset).
allowed_tools = []
disallowed_tools = []
permission_mode = "dontAsk"
# Appended system prompt for remote turns; set to "" to disable.
# persona = "..."
# Extra CLI args per backend. Caution: "--bare" can break subscription
# auth ("Not logged in") since it skips loading user configuration.
headless_extra_args = []
tmux_extra_args = []
# Pre-trust a shared folder in ~/.claude.json before forking (tmux backend),
# so the fork doesn't hit the "trust this folder?" dialog. false = don't touch.
auto_trust = true

[limits]
max_concurrent = 2
per_user_cooldown_s = 15.0
answer_timeout_s = 600.0
launch_timeout_s = 90.0
# Backstop only — colleagues close threads with !done. Forks idle longer
# than this are reaped so abandoned ones can't pile up. Set 0 to disable.
pane_idle_ttl_min = 180.0
# Answers longer than this are attached as answer.md instead of inlined.
max_inline_chars = 5500
# Per-file size cap (MB) for inbound uploads and outbound deliverables.
# 24 stays under Discord's default 25 MB limit on un-boosted servers.
max_attachment_mb = 24.0
# Most attachments accepted from a single colleague message.
max_attachments = 10

[attachments]
# The Read tool can't open Office files (.docx/.pptx/.xlsx). When a colleague
# attaches one, best-effort convert it with whatever is on PATH: "auto" picks
# the best converter found (LibreOffice -> PDF, else pandoc -> Markdown, else
# macOS textutil -> text); "off" disables it. Nothing is a hard dependency —
# PDF, images, and text always work without any converter installed.
convert = "auto"
# Advanced: a custom converter command, overriding auto-detection. {{input}}
# and {{outdir}} are substituted; the file it drops into {{outdir}} is used.
# convert_command = "soffice --headless --convert-to pdf --outdir {{outdir}} {{input}}"
'''
