#!/usr/bin/env python3
"""UserPromptSubmit hook: publish the CURRENT Claude session for agent-tunnel.

Triggers (typed as a prompt inside any Claude Code session):
- '>share'            : publish this session, mint/show a handle
- '>share <label>'    : publish with a chosen handle (e.g. >share payments)
- '>share --write <label>' : also let colleagues edit files (no shell)
- '>share --dangerously-allow-bash <label>' : also let them run shell
  commands (so a fork can build real PDFs/docx) — trusted people only
- '>share status'     : show this session's handle, if any
- '>share off'        : revoke this session's handle

The handle is what you give to colleagues; they address it in the
agent-tunnel Discord channel to talk to a read-only fork of THIS session.

Standalone (stdlib only): Claude Code may run this under a Python without the
claude_code_tools package installed. It writes the shared registry JSON read
by the `agent-tunnel serve` daemon. The schema MUST match
claude_code_tools/agent_tunnel/registry.py.
"""
import fcntl
import json
import os
import re
import sys
import time

# A codex rollout path: <codex-home>/sessions/YYYY/MM/DD/rollout-*.jsonl.
# Anchored on the whole dated-suffix so a home path that itself contains a
# `sessions` segment is neither mis-detected nor truncated (kept in sync
# with codex_session.codex_home_for / _detect logic in the package).
_CODEX_ROLLOUT_RE = re.compile(
    r"/sessions/\d{4}/\d{2}/\d{2}/rollout-[^/]*\.jsonl$"
)

TRIGGER = ">share"
# expanduser + abspath so a ~/... AGENT_TUNNEL_REGISTRY resolves to the same
# absolute file the daemon reads. NOTE: use an absolute path or ~/... — a
# RELATIVE value is anchored to this hook's cwd here but to the config-file
# dir in the daemon, so the two would diverge (relative registry paths are
# effectively unsupported).
REGISTRY_PATH = os.path.abspath(
    os.path.expanduser(
        os.environ.get("AGENT_TUNNEL_REGISTRY")
        or "~/.local/state/agent-tunnel/registry.json"
    )
)
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def _sanitize_label(label):
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    slug = slug[:32].rstrip("-")
    return slug if slug and HANDLE_RE.match(slug) else None


def _derive_handle(session_id):
    compact = session_id.replace("-", "")
    return compact[:6] if compact else "session"


def _load(path):
    """Read the registry records, tolerating corruption at any level.

    Mirrors registry.py's defensive reads: a null/non-object root, records
    table, or record must degrade to "not there", never crash — otherwise
    >share/status/off silently stop working (main() swallows exceptions).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    records = data.get("records")
    if not isinstance(records, dict):
        return {}
    return {h: r for h, r in records.items() if isinstance(r, dict)}


def _atomic_write(path, records):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"records": records}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _detect_agent(transcript_path):
    """"codex" when the transcript is a codex rollout, else "claude".

    Codex rollouts live at <codex-home>/sessions/YYYY/MM/DD/rollout-*.jsonl;
    Claude transcripts at <config-dir>/projects/<encoded-cwd>/<id>.jsonl.
    Matched on the full dated suffix so a claude path that merely contains a
    `sessions` segment is never mislabeled codex. Codex can load
    Claude-plugin hooks (same hooks.json format), so this hook may fire
    inside a codex session — label it so the daemon uses the codex backend.
    """
    return "codex" if _CODEX_ROLLOUT_RE.search(transcript_path or "") else (
        "claude"
    )


def _config_dir(transcript_path, agent):
    """Detect the agent config dir this session lives under, path-agnostically.

    Claude: everything before /projects/ (falling back to CLAUDE_CONFIG_DIR,
    then ~/.claude). Codex: the CODEX_HOME is the ancestor of the dated
    ``sessions/YYYY/MM/DD/rollout-*`` suffix — derived structurally so a home
    whose own path contains a `sessions` segment is not truncated (falling
    back to CODEX_HOME, then ~/.codex).
    """
    if agent == "codex":
        match = _CODEX_ROLLOUT_RE.search(transcript_path or "")
        if match:
            return transcript_path[: match.start()]
        env = os.environ.get("CODEX_HOME")
        if env:
            return os.path.expanduser(env)
        return os.path.expanduser("~/.codex")
    if transcript_path and "/projects/" in transcript_path:
        return transcript_path.split("/projects/")[0]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser("~/.claude")


def _publish(session_id, cwd, transcript_path, config_dir, access, label,
             agent="claude"):
    """Insert/update this session's record under a read-modify-write lock.

    access is "read"/"write", or None to preserve an existing record's level.
    """
    agent = agent or "claude"

    def _same(rec):
        return (
            rec.get("session_id") == session_id
            and (rec.get("agent") or "claude") == agent
        )

    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    lock_path = REGISTRY_PATH + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = _load(REGISTRY_PATH)
            existing = _find_by_session(records, session_id, agent)
            # Capture the prior record now: relabeling pops the old handle
            # below, so the preserved fields (access/label/created_at) must be
            # read from here, not from the new (absent) handle key.
            prior = records.get(existing) if existing else None
            if label:
                handle = label
                taken = records.get(handle)
                # A revoked handle is free for anyone to reclaim (matches
                # Registry.rename); only a LIVE handle owned by a different
                # (agent, session) is a real collision.
                if taken and not taken.get("revoked") and not _same(taken):
                    return None, handle  # collision
                if existing and existing != handle:
                    records.pop(existing, None)
            elif existing:
                handle = existing
            else:
                handle = _derive_handle(session_id)
                # Only a LIVE handle of a different (agent, session) forces a
                # suffix — a revoked one is reclaimable (same rule as label).
                while (
                    handle in records
                    and not records[handle].get("revoked")
                    and not _same(records[handle])
                ):
                    handle += "x"
            # No prior captured from THIS session above. Inherit the preserved
            # fields (access/label/created_at) only from a record this session
            # already owns (a genuine re-share); a revoked handle reclaimed by a
            # DIFFERENT session is a fresh publish and must NOT inherit the old
            # owner's access/label/created_at.
            if prior is None:
                under = records.get(handle, {})
                prior = under if _same(under) else {}
            records[handle] = {
                "handle": handle,
                "session_id": session_id,
                "cwd": cwd,
                "config_dir": config_dir,
                "agent": agent or "claude",
                # `or "read"` (not a .get default) so a pre-existing null
                # access — written by an old hook — can't persist on re-share.
                "access": access
                if access is not None
                else (prior.get("access") or "read"),
                "label": label or prior.get("label") or "",
                "transcript_path": transcript_path,
                "created_at": prior.get("created_at") or time.time(),
                "revoked": False,
            }
            _atomic_write(REGISTRY_PATH, records)
            return handle, None
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _find_by_session(records, session_id, agent="claude"):
    """Handle of the live record for THIS (agent, session), or None.

    Identity is (agent, session_id): the same session id under a different
    agent is a different owner and must not be found, relabeled, or revoked
    here. A legacy record with no agent field counts as claude.
    """
    agent = agent or "claude"
    for handle, rec in records.items():
        if (
            rec.get("session_id") == session_id
            and (rec.get("agent") or "claude") == agent
            and not rec.get("revoked")
        ):
            return handle
    return None


def _revoke(session_id, agent="claude"):
    lock_path = REGISTRY_PATH + ".lock"
    if not os.path.exists(REGISTRY_PATH):
        return None
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = _load(REGISTRY_PATH)
            handle = _find_by_session(records, session_id, agent)
            if handle:
                records[handle]["revoked"] = True
                _atomic_write(REGISTRY_PATH, records)
            return handle
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _status(session_id, agent="claude"):
    handle = _find_by_session(_load(REGISTRY_PATH), session_id, agent)
    if handle:
        return (
            f"{GREEN}This session is shared as handle: {handle}{RESET}\n"
            f"{BLUE}Colleagues: post  {handle} <question>  in the "
            f"agent-tunnel channel. Revoke with >share off.{RESET}"
        )
    return f"{BLUE}This session is not shared. Type >share to publish it.{RESET}"


def main():
    try:
        data = json.load(sys.stdin)
        session_id = data.get("session_id", "")
        prompt = data.get("prompt")
        # Absolute so the daemon (which launches the fork with cwd set to this
        # project dir and exposes upload/outbox paths via --add-dir) never has
        # to resolve a relative project path against the wrong directory.
        cwd = os.path.abspath(data.get("cwd") or os.getcwd())
        transcript_path = data.get("transcript_path", "")

        if not isinstance(prompt, str) or not prompt.strip():
            sys.exit(0)
        stripped = prompt.strip()
        low = stripped.lower()
        if low != TRIGGER and not low.startswith(TRIGGER + " "):
            sys.exit(0)

        if not session_id:
            print(json.dumps({"decision": "block", "reason": "No session ID."}))
            sys.exit(0)

        arg = stripped[len(TRIGGER):].strip()
        # Detect the agent once (from the transcript layout) so status/off
        # and publish all resolve THIS (agent, session), never the same id
        # under the other agent.
        agent = _detect_agent(transcript_path)

        if arg.lower() == "off":
            handle = _revoke(session_id, agent)
            message = (
                f"{YELLOW}Stopped sharing (handle {handle} revoked).{RESET}"
                if handle
                else f"{BLUE}This session was not shared.{RESET}"
            )
        elif arg.lower() == "status":
            message = _status(session_id, agent)
        else:
            tokens = arg.split()
            skip = "--dangerously-skip-permissions" in tokens
            bash = "--dangerously-allow-bash" in tokens
            write = "--write" in tokens or "-w" in tokens
            read = "--read" in tokens or "-r" in tokens
            # None preserves an existing record's access on re-share. "all" is
            # the strongest (skip-permissions: any tool/MCP, no prompts), then
            # bash (shell), write, read.
            access = (
                "all"
                if skip
                else (
                    "bash"
                    if bash
                    else ("write" if write else ("read" if read else None))
                )
            )
            label_raw = next((t for t in tokens if not t.startswith("-")), "")
            label = _sanitize_label(label_raw) if label_raw else ""
            if label_raw and not label:
                message = (
                    f"{YELLOW}Invalid handle. Use letters, digits, dashes "
                    f"(2-32 chars), e.g. >share payments-auth.{RESET}"
                )
            else:
                config_dir = _config_dir(transcript_path, agent)
                handle, collision = _publish(
                    session_id, cwd, transcript_path, config_dir, access,
                    label, agent
                )
                if collision:
                    message = (
                        f"{YELLOW}Handle '{collision}' is already used by "
                        f"another session. Pick a different name.{RESET}"
                    )
                else:
                    if access == "all":
                        # Each agent has its own daemon-side gate; point the
                        # owner at the section that actually unlocks it.
                        gate = "[codex]" if agent == "codex" else "[claude]"
                        note = (
                            f"\n{YELLOW}🚨 FULL ACCESS "
                            "(--dangerously-skip-permissions): the colleague's "
                            "agent can use ANY tool or MCP with NO prompts — "
                            "shell, the web, your browser, file edits. Requires "
                            f"{gate} allow_skip_permissions = true on the "
                            f"daemon. Only for people you fully trust.{RESET}"
                        )
                    elif access == "bash":
                        note = (
                            f"\n{YELLOW}⚠️ BASH access: the colleague's agent "
                            f"can RUN SHELL COMMANDS and edit files in this "
                            f"folder. Only for fully trusted people.{RESET}"
                        )
                    elif access == "write":
                        note = (
                            f"\n{YELLOW}WRITE access: colleagues can edit "
                            f"files in this folder.{RESET}"
                        )
                    else:
                        note = ""
                    message = (
                        f"{GREEN}Sharing this session as: {handle}{RESET}{note}"
                        f"\n{BLUE}Give colleagues this handle; they post  "
                        f"{handle} <question>  in the agent-tunnel channel.\n"
                        f"Revoke anytime with >share off.{RESET}"
                    )

        print(json.dumps({"decision": "block", "reason": message}))
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
