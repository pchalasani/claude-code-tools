"""Re-target a Codex rollout at another model provider.

Codex records the model provider a thread was created with in the
rollout's ``session_meta`` line (``"model_provider": "openai"``), and the
Codex TUI refuses to resume a thread under a different provider
("This chat uses openai, but your current provider is ..."). This module
writes a NEW rollout, forked from the original, that is bound to the
requested provider instead, so a session can move between OpenAI and a
local model (or between any two providers) in either direction.

Everything in the transcript is kept except what one provider cannot
take from another: reasoning items (OpenAI's are encrypted; a local
model's carry raw ``reasoning_text`` content, which OpenAI rejects as
input) and any item carrying ``encrypted_content``, such as OpenAI's
encrypted compaction summaries. They are dropped from the top-level
history and from the ``replacement_history`` / ``guardian_history``
lists inside ``compacted`` records. Visible messages, tool calls, tool
output and plaintext summaries all survive.

Codex also resumes with the model a session last used, which belongs to
the old provider; :class:`RetargetStats` reports it so the caller can
tell the user to pick a model with ``-m``.

The source rollout is never modified. The new rollout goes to the same
Codex home as the source (or ``codex_home`` when given) and is appended
to that home's ``history.jsonl`` like every other ported session.
"""

from __future__ import annotations

import datetime
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from claude_code_tools.port_claude_to_codex import (
    _append_history_transactional,
    _new_codex_session_id,
    _utc_now_codex_timestamp,
)
from claude_code_tools.port_codex_to_claude import _write_transcript_atomic
from claude_code_tools.session_utils import get_codex_home

# Provider ids are TOML table keys in Codex's config ([model_providers.X])
# and appear unquoted on resume command lines.
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# History lists inside a ``compacted`` record that hold response items.
_COMPACTED_HISTORY_KEYS = ("replacement_history", "guardian_history")


@dataclass
class RetargetStats:
    """What changed while re-targeting a rollout.

    Attributes:
        source_provider: Provider recorded in the source rollout.
        dropped: Count of dropped provider-only items, by item type
            (e.g. ``{"reasoning": 192, "compaction": 2}``).
        last_model: Model of the session's last turn ("" if none).
    """

    source_provider: str
    dropped: Counter = field(default_factory=Counter)
    last_model: str = ""


def validate_provider(provider: str) -> str:
    """Check a provider id and return it.

    Args:
        provider: Provider id such as ``openai`` or ``local-fractal``.

    Returns:
        The provider id, unchanged.

    Raises:
        ValueError: If the id is empty or not a plain identifier.
    """
    if not _PROVIDER_RE.match(provider or ""):
        raise ValueError(
            f"Invalid provider {provider!r}: use letters, digits, '-' or '_' "
            "(e.g. openai, local-fractal)"
        )
    return provider


def _is_provider_only(item: Any) -> bool:
    """True for a response item another provider cannot take as input."""
    return isinstance(item, dict) and (
        item.get("type") == "reasoning" or bool(item.get("encrypted_content")))


def _codex_home_of(rollout: Path) -> Optional[Path]:
    """Codex home holding ``rollout``.

    Live rollouts sit in ``<home>/sessions/Y/M/D/``; archived ones in
    ``<home>/archived_sessions/``.
    """
    parents = rollout.parents
    if len(parents) >= 5 and parents[3].name == "sessions":
        return parents[4]
    if len(parents) >= 2 and parents[0].name == "archived_sessions":
        return parents[1]
    return None


def _read_session_meta(rollout: Path) -> dict[str, Any]:
    """Return the first line's session_meta payload, or raise."""
    with open(rollout, "r", encoding="utf-8") as f:
        first = f.readline()
    try:
        record = json.loads(first)
    except ValueError:
        record = None
    if not isinstance(record, dict) or record.get("type") != "session_meta" \
            or not isinstance(record.get("payload"), dict):
        raise ValueError(f"Not a Codex rollout (no session_meta first): {rollout}")
    return record["payload"]


def _first_user_text(record: dict[str, Any]) -> Optional[str]:
    """The text of a user_message event, if ``record`` is one."""
    payload = record.get("payload")
    if record.get("type") == "event_msg" and isinstance(payload, dict) \
            and payload.get("type") == "user_message":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return None


def _retarget_lines(
    rollout: Path,
    *,
    provider: str,
    new_id: str,
    now_iso: str,
    stats: RetargetStats,
    preview: dict[str, Optional[str]],
) -> Iterator[dict[str, Any]]:
    """Stream the source rollout's records, re-targeted at ``provider``."""
    first = True
    with open(rollout, "r", encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            record = json.loads(raw)
            if not isinstance(record, dict):
                yield record
                continue
            kind = record.get("type")
            payload = record.get("payload")
            if kind == "session_meta" and isinstance(payload, dict):
                payload = {**payload, "model_provider": provider}
                if first:
                    old_id = payload.get("id")
                    payload.update(id=new_id, session_id=new_id,
                                   timestamp=now_iso, forked_from_id=old_id)
                    record = {
                        **record,
                        "timestamp": now_iso,
                        "payload": payload,
                        "continue_metadata": {
                            "parent_session_id": old_id,
                            "parent_session_file": str(rollout.absolute()),
                            "continued_at": now_iso,
                            "retargeted_from_provider": stats.source_provider,
                            "retargeted_to_provider": provider,
                        },
                    }
                else:  # lineage line of the thread this one forked from
                    record = {**record, "payload": payload}
                first = False
            elif kind == "turn_context" and isinstance(payload, dict):
                if isinstance(payload.get("model"), str):
                    stats.last_model = payload["model"]
            elif kind == "response_item" and _is_provider_only(payload):
                stats.dropped[str(payload.get("type"))] += 1
                continue
            elif kind == "compacted" and isinstance(payload, dict):
                payload = dict(payload)
                for key in _COMPACTED_HISTORY_KEYS:
                    items = payload.get(key)
                    if isinstance(items, list):
                        kept = []
                        for x in items:
                            if isinstance(x, dict) and _is_provider_only(x):
                                stats.dropped[str(x.get("type"))] += 1
                            else:
                                kept.append(x)
                        payload[key] = kept
                record = {**record, "payload": payload}
            elif kind == "event_msg" and isinstance(payload, dict) \
                    and payload.get("type") == "thread_settings_applied":
                settings = payload.get("thread_settings")
                if isinstance(settings, dict) and "model_provider_id" in settings:
                    record = {**record, "payload": {
                        **payload,
                        "thread_settings": {**settings,
                                            "model_provider_id": provider}}}
            if preview["text"] is None:
                preview["text"] = _first_user_text(record)
            yield record


def retarget_codex_session(
    rollout_file: Union[str, Path],
    provider: str,
    codex_home: Optional[Union[str, Path]] = None,
) -> tuple[str, Path, RetargetStats]:
    """Write a copy of a Codex rollout bound to another model provider.

    Args:
        rollout_file: Path to the source Codex rollout JSONL file.
        provider: Target provider id (``openai``, ``local-fractal``, ...).
        codex_home: Codex home for the new rollout. Defaults to the
            source rollout's own home, else ``~/.codex``.

    Returns:
        Tuple ``(new_session_id, new_file_path, stats)``.

    Raises:
        FileNotFoundError: If the source rollout does not exist.
        ValueError: If the provider id is invalid, the file is not a
            Codex rollout, or it already uses ``provider``.
        OSError: On filesystem failures (no partial file is left, and
            the history entry is rolled back).
    """
    validate_provider(provider)
    source = Path(rollout_file).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Codex session not found: {source}")
    meta = _read_session_meta(source)
    source_provider = str(meta.get("model_provider") or "openai")
    if source_provider == provider:
        raise ValueError(
            f"Session already uses provider {provider!r}; nothing to change"
        )

    if codex_home is not None:
        home_dir = get_codex_home(str(codex_home))
    else:
        home_dir = _codex_home_of(source.absolute()) or get_codex_home(None)
    new_id = _new_codex_session_id()
    now_iso = _utc_now_codex_timestamp()
    now_local = datetime.datetime.now()
    day_dir = home_dir / "sessions" / now_local.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_local.strftime("%Y-%m-%dT%H-%M-%S")
    out_path = day_dir / f"rollout-{stamp}-{new_id}.jsonl"

    stats = RetargetStats(source_provider=source_provider)
    preview: dict[str, Optional[str]] = {"text": None}
    _write_transcript_atomic(
        out_path,
        _retarget_lines(source, provider=provider, new_id=new_id,
                        now_iso=now_iso, stats=stats, preview=preview),
    )
    _append_history_transactional(
        home_dir, new_id,
        preview["text"] or f"Codex session moved to provider {provider}",
        out_path,
    )
    return new_id, out_path, stats
