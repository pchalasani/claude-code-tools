"""Pydantic models for Claude Code and OpenCode configurations."""

from pathlib import Path
from pydantic import BaseModel, Field


# =============================================================================
# Claude Code Models (Input)
# =============================================================================


class ClaudeCodeHook(BaseModel):
    """A single hook definition from Claude Code settings.json."""

    event: str = Field(description="Hook event: PreToolUse, PostToolUse, etc.")
    matcher: str | None = Field(
        default=None, description="Tool matcher pattern (e.g., 'Edit|Write')"
    )
    hook_type: str = Field(description="Hook type: 'command' or 'prompt'")
    command: str | None = Field(default=None, description="Bash command to execute")
    prompt: str | None = Field(default=None, description="LLM prompt for evaluation")
    timeout: int = Field(default=60, description="Timeout in seconds")


class ClaudeCodeAgent(BaseModel):
    """A Claude Code agent definition from .claude/agents/*.md."""

    name: str = Field(description="Agent name (from filename or frontmatter)")
    description: str = Field(description="When to use this agent")
    tools: list[str] | None = Field(
        default=None, description="Comma-separated tool list"
    )
    model: str | None = Field(
        default=None, description="Model: sonnet, opus, haiku, inherit"
    )
    permission_mode: str | None = Field(
        default=None,
        description="Permission mode: default, acceptEdits, dontAsk, plan",
    )
    skills: list[str] | None = Field(
        default=None, description="Skills to auto-load"
    )
    prompt: str = Field(description="The agent's system prompt (markdown body)")


class ClaudeCodeCommand(BaseModel):
    """A Claude Code slash command from .claude/commands/*.md."""

    name: str = Field(description="Command name (from filename)")
    description: str | None = Field(default=None, description="Brief description")
    allowed_tools: str | None = Field(
        default=None, description="Tools the command can use"
    )
    argument_hint: str | None = Field(
        default=None, description="Expected arguments hint"
    )
    model: str | None = Field(default=None, description="Specific model to use")
    template: str = Field(description="The command template (markdown body)")


class ClaudeCodeSkill(BaseModel):
    """A Claude Code skill from .claude/skills/*/SKILL.md."""

    name: str = Field(description="Skill name (from directory name)")
    description: str = Field(description="Skill description")
    path: Path = Field(description="Path to the skill directory")
    content: str = Field(description="Full SKILL.md content")


class ClaudeCodeMcpServer(BaseModel):
    """An MCP server configuration from Claude Code."""

    name: str = Field(description="Server name")
    server_type: str = Field(description="Server type: stdio, http, sse")
    command: str | None = Field(default=None, description="Command for stdio servers")
    args: list[str] | None = Field(default=None, description="Arguments for command")
    url: str | None = Field(default=None, description="URL for http/sse servers")
    env: dict[str, str] | None = Field(
        default=None, description="Environment variables"
    )
    headers: dict[str, str] | None = Field(
        default=None, description="HTTP headers"
    )


class ClaudeCodeConfig(BaseModel):
    """Complete Claude Code configuration detected in a project."""

    hooks: list[ClaudeCodeHook] = Field(default_factory=list)
    agents: list[ClaudeCodeAgent] = Field(default_factory=list)
    commands: list[ClaudeCodeCommand] = Field(default_factory=list)
    skills: list[ClaudeCodeSkill] = Field(default_factory=list)
    mcp_servers: list[ClaudeCodeMcpServer] = Field(default_factory=list)
    claude_md: str | None = Field(
        default=None, description="Content of CLAUDE.md if present"
    )


# =============================================================================
# OpenCode Models (Output)
# =============================================================================


class OpenCodeAgent(BaseModel):
    """An OpenCode agent definition for .opencode/agent/*.md."""

    name: str
    description: str
    mode: str = "subagent"  # primary | subagent | all
    model: str | None = None  # provider/model-id format
    temperature: float | None = None
    top_p: float | None = None
    color: str | None = None
    hidden: bool = False
    steps: int | None = None
    permission: dict | None = None
    prompt: str = ""


class OpenCodeCommand(BaseModel):
    """An OpenCode command definition for .opencode/command/*.md."""

    name: str
    description: str
    agent: str | None = None
    model: str | None = None
    subtask: bool = False
    template: str = ""


class OpenCodeMcpServer(BaseModel):
    """An OpenCode MCP server configuration."""

    name: str
    server_type: str  # "local" or "remote"
    command: list[str] | None = None  # For local servers
    url: str | None = None  # For remote servers
    environment: dict[str, str] | None = None
    headers: dict[str, str] | None = None


# =============================================================================
# Translation Mappings
# =============================================================================


# Claude Code model names to OpenCode format
MODEL_MAPPING = {
    "sonnet": "anthropic/claude-sonnet-4-20250514",
    "opus": "anthropic/claude-opus-4-5-20251101",
    "haiku": "anthropic/claude-3-5-haiku-20241022",
    "inherit": None,
}

# Claude Code hook events to OpenCode plugin hooks
HOOK_EVENT_MAPPING = {
    "PreToolUse": "tool.execute.before",
    "PostToolUse": "tool.execute.after",
    "Stop": "event",  # session.idle
    "SubagentStop": "event",  # session events
    "SessionStart": "event",  # session.created
    "SessionEnd": "event",  # session.deleted
    "UserPromptSubmit": "chat.message",
    "PreCompact": "experimental.session.compacting",
    "Notification": "event",  # tui.toast.show
    "PermissionRequest": "permission.ask",
}

# Claude Code permission modes to OpenCode permission config
PERMISSION_MODE_MAPPING = {
    "default": {},
    "acceptEdits": {"edit": "allow", "bash": "ask"},
    "dontAsk": {"edit": "allow", "bash": "allow", "read": "allow"},
    "bypassPermissions": {"edit": "allow", "bash": "allow", "read": "allow"},
    "plan": {"edit": "deny", "bash": "deny", "read": "allow"},
}

# Claude Code MCP types to OpenCode types
MCP_TYPE_MAPPING = {
    "stdio": "local",
    "http": "remote",
    "sse": "remote",
}
