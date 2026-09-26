"""Small, dependency-free System One transport for Jev and compatible servers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class DetectorError(ValueError):
    """An input, configuration, or inference failure; never a clean result."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward authorization or prose to a redirected endpoint."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        """Reject redirects rather than changing the configured destination."""
        return None


def cloudflare_account() -> str:
    """Read an environment account or the existing sysone configuration."""
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not account:
        path = Path(os.environ.get(
            "SYSONE_CONFIG", str(Path.home() / ".config/sysone/config.toml")
        )).expanduser()
        if path.exists():
            with path.open("rb") as stream:
                config = tomllib.load(stream)
            account = config.get("cloudflare", {}).get("account_id")
    if not isinstance(account, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", account):
        raise DetectorError("Set CLOUDFLARE_ACCOUNT_ID (32 hex characters).")
    return account


def cloudflare_token() -> str:
    """Use an explicit token or the user's existing Wrangler login."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return token
    command = (["wrangler"] if shutil.which("wrangler")
               else ["npx", "--no-install", "wrangler"])
    try:
        # Wrangler loads .env at its working directory, even for auth token.
        # Keep npm resolution unchanged but isolate Wrangler from repo config.
        with TemporaryDirectory(prefix="jev-prose-auth-") as auth_dir:
            result = subprocess.run(
                [*command, "auth", "token", "--json", "--cwd", auth_dir],
                capture_output=True, text=True, timeout=30, check=True,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DetectorError(
            "Set CLOUDFLARE_API_TOKEN or log in with Wrangler."
        ) from exc
    try:
        token = json.loads(result.stdout)["token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise DetectorError("Wrangler returned no JSON token.") from exc
    if not isinstance(token, str) or not token.strip():
        raise DetectorError("Wrangler returned an empty token.")
    return token


@dataclass
class Backend:
    """Send batched noul questions using a resolved, immutable configuration."""

    url: str
    model: str
    cloudflare: bool = False
    token: str | None = field(default=None, repr=False)
    timeout: float = 90.0
    attempted: bool = field(default=False, init=False)

    def decide(
        self, state: dict[str, str], questions: dict[str, Any]
    ) -> dict[str, Any]:
        """Call the endpoint; reject HTTP, gateway, and malformed JSON failures."""
        payload: dict[str, Any] = {"state": state, "questions": questions}
        if self.cloudflare:
            payload = {"model": self.model, "input": payload}
        else:
            payload["model"] = self.model
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers
        )
        self.attempted = True
        try:
            with urllib.request.build_opener(NoRedirect).open(
                request, timeout=self.timeout
            ) as response:
                if response.status != 200:
                    raise DetectorError(f"Endpoint returned HTTP {response.status}.")
                data = response.read(8_000_001)
                if len(data) > 8_000_000:
                    raise DetectorError("Endpoint response exceeds 8 MB.")
                value = json.loads(data)
        except urllib.error.HTTPError as exc:
            hint = " Check AI Gateway credit." if exc.code == 402 else ""
            if exc.code == 401 and self.cloudflare:
                hint = (" Check CLOUDFLARE_API_TOKEN in the process environment; "
                        "it overrides the global Wrangler login. If absent, "
                        "verify that login with wrangler whoami.")
            raise DetectorError(f"Endpoint returned HTTP {exc.code}.{hint}") from exc
        except (OSError, ValueError, urllib.error.URLError) as exc:
            if isinstance(exc, DetectorError):
                raise
            raise DetectorError("Endpoint failed or returned invalid JSON.") from exc
        if not isinstance(value, dict):
            raise DetectorError("Endpoint response is not an object.")
        if self.cloudflare:
            if value.get("success") is not True:
                raise DetectorError("Cloudflare reported an unsuccessful request.")
            value = value.get("result")
            if not isinstance(value, dict):
                raise DetectorError("Cloudflare returned no result object.")
            if value.get("state") not in (None, "Completed"):
                raise DetectorError("Cloudflare inference did not complete.")
            value = value.get("result", value)
        if not isinstance(value, dict):
            raise DetectorError("Endpoint returned no inference object.")
        return value


def make_backend(
    name: str, url: str | None, model: str | None, timeout: float
) -> Backend:
    """Resolve portable CLI/environment settings without importing sysone."""
    if url:
        if not url.startswith(("https://", "http://")):
            raise DetectorError("--url must be an HTTP(S) System One endpoint.")
        return Backend(url, model or "jev-latest", token=os.environ.get(
            "JEV_PROSE_API_KEY"
        ), timeout=timeout)
    if name == "typesafe":
        token = os.environ.get("TYPESAFE_API_KEY")
        if not token:
            raise DetectorError("Set TYPESAFE_API_KEY.")
        return Backend(
            "https://api.typesafe.ai/v1/systemone", model or "jev-latest",
            token=token, timeout=timeout,
        )
    account = cloudflare_account()
    return Backend(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run",
        model or "typesafe/jev", cloudflare=True,
        token=cloudflare_token(), timeout=timeout,
    )
