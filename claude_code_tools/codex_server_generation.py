"""Deterministic identities for independently running app-server generations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Sequence


GENERATION_PATTERN = re.compile(r"[0-9a-f]{24}")


def server_generation(
    codex_path: str,
    executable_identity: str,
    codex_version: str,
    plugin_fingerprint: str,
    codex_options: Sequence[str],
) -> str:
    """Return the generation selected by every server-affecting input.

    Args:
        codex_path: Canonical path to the selected Codex executable.
        executable_identity: Stable identity of the executable file.
        codex_version: Version reported by that executable.
        plugin_fingerprint: Fingerprint of the effective plugin configuration.
        codex_options: Global Codex options forwarded to the app server.

    Returns:
        A short lowercase hexadecimal generation identifier.
    """
    payload = json.dumps(
        {
            "codexOptions": list(codex_options),
            "codexPath": codex_path,
            "codexVersion": codex_version,
            "executableIdentity": executable_identity,
            "pluginFingerprint": plugin_fingerprint,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def validate_generation(value: str) -> str:
    """Validate an app-server generation received through the environment."""
    if GENERATION_PATTERN.fullmatch(value) is None:
        raise ValueError("app-server generation must be 24 lowercase hex characters")
    return value
