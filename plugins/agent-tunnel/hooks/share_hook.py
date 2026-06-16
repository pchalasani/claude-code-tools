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

TRIGGER = ">share"
REGISTRY_PATH = os.environ.get("AGENT_TUNNEL_REGISTRY") or os.path.expanduser(
    "~/.local/state/agent-tunnel/registry.json"
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("records", {})
    except (OSError, ValueError):
        return {}


def _atomic_write(path, records):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"records": records}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _config_dir(transcript_path):
    """Detect the Claude config dir this session lives under, path-agnostically.

    The transcript path is <config-dir>/projects/<encoded-cwd>/<id>.jsonl, so
    the config dir is everything before /projects/. Falls back to
    CLAUDE_CONFIG_DIR, then ~/.claude.
    """
    if transcript_path and "/projects/" in transcript_path:
        return transcript_path.split("/projects/")[0]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser("~/.claude")


def _publish(session_id, cwd, transcript_path, config_dir, access, label):
    """Insert/update this session's record under a read-modify-write lock.

    access is "read"/"write", or None to preserve an existing record's level.
    """
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    lock_path = REGISTRY_PATH + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = _load(REGISTRY_PATH)
            existing = _find_by_session(records, session_id)
            if label:
                handle = label
                taken = records.get(handle)
                if taken and taken.get("session_id") != session_id:
                    return None, handle  # collision
                if existing and existing != handle:
                    records.pop(existing, None)
            elif existing:
                handle = existing
            else:
                handle = _derive_handle(session_id)
                while (
                    handle in records
                    and records[handle].get("session_id") != session_id
                ):
                    handle += "x"
            records[handle] = {
                "handle": handle,
                "session_id": session_id,
                "cwd": cwd,
                "config_dir": config_dir,
                # `or "read"` (not a .get default) so a pre-existing null
                # access — written by an old hook — can't persist on re-share.
                "access": access
                if access is not None
                else (records.get(handle, {}).get("access") or "read"),
                "label": label or records.get(handle, {}).get("label", ""),
                "transcript_path": transcript_path,
                "created_at": records.get(handle, {}).get(
                    "created_at", time.time()
                ),
                "revoked": False,
            }
            _atomic_write(REGISTRY_PATH, records)
            return handle, None
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _find_by_session(records, session_id):
    for handle, rec in records.items():
        if rec.get("session_id") == session_id and not rec.get("revoked"):
            return handle
    return None


def _revoke(session_id):
    lock_path = REGISTRY_PATH + ".lock"
    if not os.path.exists(REGISTRY_PATH):
        return None
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = _load(REGISTRY_PATH)
            handle = _find_by_session(records, session_id)
            if handle:
                records[handle]["revoked"] = True
                _atomic_write(REGISTRY_PATH, records)
            return handle
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _status(session_id):
    handle = _find_by_session(_load(REGISTRY_PATH), session_id)
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

        if arg.lower() == "off":
            handle = _revoke(session_id)
            message = (
                f"{YELLOW}Stopped sharing (handle {handle} revoked).{RESET}"
                if handle
                else f"{BLUE}This session was not shared.{RESET}"
            )
        elif arg.lower() == "status":
            message = _status(session_id)
        else:
            tokens = arg.split()
            bash = "--dangerously-allow-bash" in tokens
            write = "--write" in tokens or "-w" in tokens
            read = "--read" in tokens or "-r" in tokens
            # None preserves an existing record's access on re-share. bash is
            # the strongest level (also runs shell commands), then write, read.
            access = (
                "bash"
                if bash
                else ("write" if write else ("read" if read else None))
            )
            label_raw = next((t for t in tokens if not t.startswith("-")), "")
            label = _sanitize_label(label_raw) if label_raw else ""
            if label_raw and not label:
                message = (
                    f"{YELLOW}Invalid handle. Use letters, digits, dashes "
                    f"(2-32 chars), e.g. >share payments-auth.{RESET}"
                )
            else:
                config_dir = _config_dir(transcript_path)
                handle, collision = _publish(
                    session_id, cwd, transcript_path, config_dir, access, label
                )
                if collision:
                    message = (
                        f"{YELLOW}Handle '{collision}' is already used by "
                        f"another session. Pick a different name.{RESET}"
                    )
                else:
                    if access == "bash":
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
