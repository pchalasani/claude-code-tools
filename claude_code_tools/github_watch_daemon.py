"""One shared daemon that wakes Codex when watched GitHub issues get replies."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType

from claude_code_tools.codex_server_process import process_identity
from claude_code_tools.github_watch_store import (
    IssueWatch,
    RepositoryCursor,
    WatchStore,
)
from claude_code_tools.issue_reply_delivery import (
    IssueReply,
    deliver_issue_reply,
)

POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 5.0
GH_TIMEOUT_SECONDS = 20.0
MAX_GH_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_COMMENT_TEXT_BYTES = 4096
CURSOR_OVERLAP_SECONDS = 5 * 60
CURSOR_ROTATION_SECONDS = 10 * 60


@dataclass(frozen=True)
class GitHubPage:
    """One HTTP response returned by ``gh api --include``."""

    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class RepositoryPoll:
    """Comments and cache metadata from one repository poll."""

    comments: list[IssueReply]
    cursor_key: str
    cursor: RepositoryCursor
    polled_at: str
    etag: str | None
    cacheable: bool
    cursor_stale: bool


class GitHubWatchDaemon:
    """Poll repositories and deliver the first reply for each pending watch."""

    def __init__(self, store: WatchStore) -> None:
        """Initialize one daemon against shared durable state."""
        self.store = store
        self.instance_id = str(uuid.uuid4())
        self.pid = os.getpid()
        identity = process_identity(self.pid)
        if identity is None:
            raise RuntimeError("cannot establish watcher process identity")
        self.process_identity = identity
        self.started_at = _now()
        self.running = True
        self._stop_event = threading.Event()

    def run(self) -> int:
        """Hold the singleton lock and process watches until terminated."""
        lock_fd = os.open(self.store.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            self._install_signal_handlers()
            self._heartbeat()
            next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
            while self.running:
                self._poll_once()
                now = time.monotonic()
                if now >= next_heartbeat:
                    self._heartbeat()
                    next_heartbeat = now + HEARTBEAT_SECONDS
                self._stop_event.wait(POLL_SECONDS)
            return 0
        finally:
            os.close(lock_fd)

    def _install_signal_handlers(self) -> None:
        """Arrange cooperative daemon shutdown."""
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

    def _stop(self, _signal: int, _frame: FrameType | None) -> None:
        """Stop after the current bounded poll or delivery attempt."""
        self.running = False
        self._stop_event.set()

    def _heartbeat(self) -> None:
        """Publish the current lock owner's native process identity."""
        self.store.heartbeat(
            self.instance_id,
            self.pid,
            self.process_identity,
            self.started_at,
        )

    def _poll_once(self) -> None:
        """Poll each GitHub context once and deliver matching replies."""
        grouped: dict[tuple[str, str, str | None], list[IssueWatch]] = defaultdict(
            list
        )
        for watch in self.store.pending_watches():
            grouped[
                (watch.github_host, watch.repository, watch.github_config_dir)
            ].append(watch)
        for key, watches in grouped.items():
            if not self.running:
                return
            try:
                poll = self._repository_comments(key, watches)
            except Exception as exc:
                diagnostic = _diagnostic(exc)
                for watch in watches:
                    self.store.mark_retry(watch.watch_id, diagnostic)
                continue
            self.store.mark_poll_succeeded(
                [watch.watch_id for watch in watches]
            )
            by_issue: dict[int, list[IssueReply]] = defaultdict(list)
            for comment in poll.comments:
                by_issue[comment.issue_number].append(comment)
            delivery_failed = False
            for watch in watches:
                candidates = [
                    comment
                    for comment in by_issue.get(watch.issue_number, [])
                    if _at_or_after(comment.created_at, watch.registered_at)
                ]
                if not candidates:
                    continue
                comment = min(candidates, key=lambda item: item.comment_id)
                if not self._deliver(watch, comment):
                    delivery_failed = True
            self._save_poll_state(poll, delivery_failed)

    def _repository_comments(
        self,
        key: tuple[str, str, str | None],
        watches: list[IssueWatch],
    ) -> RepositoryPoll:
        """Fetch comments since the durable cursor for one repository context."""
        host, repository, config_dir = key
        cursor_key = "\x1f".join((host, repository, config_dir or ""))
        fallback = min(_minus_overlap(watch.registered_at) for watch in watches)
        cursor = self.store.repository_cursor(cursor_key, fallback)
        polled_at = _now()
        cursor_stale = _cursor_is_stale(cursor.since_at, polled_at)
        first_url = f"repos/{repository}/issues/comments"
        fields = [f"since={cursor.since_at}", "per_page=100", "page=1"]
        environment = dict(os.environ)
        if config_dir:
            environment["GH_CONFIG_DIR"] = config_dir
        else:
            environment.pop("GH_CONFIG_DIR", None)
        pages = self._fetch_pages(
            host,
            first_url,
            fields,
            None if cursor_stale else cursor.etag,
            environment,
        )
        if pages[0].status_code == 304:
            return RepositoryPoll(
                comments=[],
                cursor_key=cursor_key,
                cursor=cursor,
                polled_at=polled_at,
                etag=pages[0].headers.get("etag") or cursor.etag,
                cacheable=True,
                cursor_stale=False,
            )
        comments = _parse_comments(b"\n".join(page.body for page in pages))
        cacheable = len(pages) == 1 and len(comments) < 100
        return RepositoryPoll(
            comments=comments,
            cursor_key=cursor_key,
            cursor=cursor,
            polled_at=polled_at,
            etag=pages[0].headers.get("etag") if cacheable else None,
            cacheable=cacheable,
            cursor_stale=cursor_stale,
        )

    def _fetch_pages(
        self,
        host: str,
        first_url: str,
        fields: list[str],
        etag: str | None,
        environment: dict[str, str],
    ) -> list[GitHubPage]:
        """Fetch bounded GitHub pages while preserving HTTP response metadata."""
        pages: list[GitHubPage] = []
        next_url: str | None = first_url
        total_bytes = 0
        while next_url is not None:
            command = ["gh", "api", "--include", "--hostname", host]
            if not pages:
                command.extend(["--method", "GET", next_url])
                for field in fields:
                    command.extend(["-f", field])
                if etag:
                    command.extend(["-H", f"If-None-Match: {etag}"])
            else:
                command.append(next_url)
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=GH_TIMEOUT_SECONDS,
                check=False,
            )
            total_bytes += len(completed.stdout)
            if total_bytes > MAX_GH_OUTPUT_BYTES:
                raise RuntimeError("GitHub returned too many issue comments")
            try:
                page = _parse_http_response(completed.stdout)
            except RuntimeError as exc:
                if completed.returncode == 0:
                    raise
                error = completed.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub comment poll failed: {error.strip()}"
                ) from exc
            if completed.returncode != 0 and page.status_code != 304:
                error = completed.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub comment poll failed: {error.strip()}"
                )
            if page.status_code not in {200, 304}:
                raise RuntimeError(
                    "GitHub comment poll returned unexpected HTTP status "
                    f"{page.status_code}"
                )
            pages.append(page)
            if page.status_code == 304:
                break
            next_url = _next_page_url(page.headers.get("link"))
            if len(pages) > 1000:
                raise RuntimeError("GitHub returned too many comment pages")
        return pages

    def _save_poll_state(
        self,
        poll: RepositoryPoll,
        delivery_failed: bool,
    ) -> None:
        """Save cache state without advancing past an undelivered reply."""
        if delivery_failed:
            self.store.update_repository_cursor(
                poll.cursor_key,
                poll.cursor.since_at,
                None,
            )
            return
        if poll.cursor_stale:
            self.store.update_repository_cursor(
                poll.cursor_key,
                _minus_overlap(poll.polled_at),
                None,
            )
            return
        self.store.update_repository_cursor(
            poll.cursor_key,
            poll.cursor.since_at,
            poll.etag if poll.cacheable else None,
        )

    def _deliver(self, watch: IssueWatch, comment: IssueReply) -> bool:
        """Deliver one idempotent reply notification into the origin thread."""
        try:
            deliver_issue_reply(watch, comment)
        except Exception as exc:
            self.store.mark_retry(watch.watch_id, _diagnostic(exc))
            return False
        self.store.mark_delivered(watch.watch_id, comment.comment_id, comment.url)
        return True


def _parse_http_response(raw: bytes) -> GitHubPage:
    """Parse the header block emitted by one ``gh api --include`` call."""
    separator = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
    try:
        header_block, body = raw.split(separator, 1)
    except ValueError as exc:
        raise RuntimeError("GitHub returned malformed HTTP output") from exc
    lines = header_block.decode("iso-8859-1").splitlines()
    if not lines or not lines[0].startswith("HTTP/"):
        raise RuntimeError("GitHub returned malformed HTTP status")
    parts = lines[0].split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise RuntimeError("GitHub returned malformed HTTP status")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, delimiter, value = line.partition(":")
        if delimiter:
            headers[name.strip().lower()] = value.strip()
    return GitHubPage(status_code=int(parts[1]), headers=headers, body=body)


def _next_page_url(link: str | None) -> str | None:
    """Return GitHub's exact next-page URL without constructing pagination."""
    if not link:
        return None
    for part in link.split(","):
        url, separator, attributes = part.strip().partition(">")
        if separator and 'rel="next"' in attributes:
            return url.removeprefix("<")
    return None


def _cursor_is_stale(since_at: str, observed_at: str) -> bool:
    """Return whether a stable conditional window should rotate forward."""
    try:
        since = datetime.fromisoformat(since_at.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    age = observed.astimezone(UTC) - since.astimezone(UTC)
    return age.total_seconds() >= CURSOR_ROTATION_SECONDS


def _parse_comments(raw: bytes) -> list[IssueReply]:
    """Validate and bound the paginated GitHub response."""
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GitHub returned invalid comment JSON") from exc
    pages = _decode_json_pages(text)
    comments: list[IssueReply] = []
    for page in pages:
        if not isinstance(page, list):
            raise RuntimeError("GitHub returned invalid comment page data")
        for value in page:
            comment = _parse_comment(value)
            if comment is not None:
                comments.append(comment)
            if len(comments) > 10_000:
                raise RuntimeError("GitHub returned too many issue comments")
    return comments


def _decode_json_pages(value: str) -> list[object]:
    """Decode the adjacent JSON values emitted by older gh pagination."""
    decoder = json.JSONDecoder()
    pages: list[object] = []
    index = 0
    while index < len(value):
        while index < len(value) and value[index].isspace():
            index += 1
        if index == len(value):
            break
        try:
            page, index = decoder.raw_decode(value, index)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GitHub returned invalid comment JSON") from exc
        pages.append(page)
        if len(pages) > 1000:
            raise RuntimeError("GitHub returned too many comment pages")
    return pages


def _parse_comment(value: object) -> IssueReply | None:
    """Project one GitHub API comment to bounded notification fields."""
    if not isinstance(value, dict):
        return None
    issue_url = value.get("issue_url")
    comment_id = value.get("id")
    html_url = value.get("html_url")
    created_at = value.get("created_at")
    user = value.get("user")
    body = value.get("body")
    if (
        not isinstance(issue_url, str)
        or not isinstance(comment_id, int)
        or isinstance(comment_id, bool)
        or not isinstance(html_url, str)
        or not isinstance(created_at, str)
        or not isinstance(user, dict)
        or not isinstance(user.get("login"), str)
        or not isinstance(body, str)
    ):
        return None
    try:
        issue_number = int(issue_url.rstrip("/").rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return None
    return IssueReply(
        comment_id=comment_id,
        issue_number=issue_number,
        url=html_url[:2048],
        author=user["login"][:256],
        body=_truncate_utf8(body, MAX_COMMENT_TEXT_BYTES),
        created_at=created_at,
    )


def _truncate_utf8(value: str, limit: int) -> str:
    """Return text fitting one UTF-8 byte budget."""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = "\n[comment truncated]"
    budget = limit - len(suffix.encode("utf-8"))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix


def _minus_overlap(value: str) -> str:
    """Move a cursor back enough for GitHub's eventually consistent index."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    overlapped = parsed.astimezone(UTC) - timedelta(
        seconds=CURSOR_OVERLAP_SECONDS
    )
    return overlapped.isoformat(timespec="microseconds")


def _at_or_after(value: str, threshold: str) -> bool:
    """Compare a second-precision GitHub timestamp without losing replies."""
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        registered = datetime.fromisoformat(threshold.replace("Z", "+00:00"))
    except ValueError:
        return False
    registration_second = registered.astimezone(UTC).replace(microsecond=0)
    return observed.astimezone(UTC) >= registration_second


def _diagnostic(error: object) -> str:
    """Return a bounded single-line error diagnostic."""
    return " ".join(str(error).split())[:4096]


def _now() -> str:
    """Return a stable UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _configure_logging(path: Path) -> None:
    """Configure one bounded append-only daemon log."""
    if path.exists() and path.stat().st_size > 1024 * 1024:
        path.replace(path.with_suffix(".log.previous"))
    logging.basicConfig(
        filename=path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def main() -> None:
    """Run the shared watcher daemon."""
    store = WatchStore()
    _configure_logging(store.log_path)
    try:
        status = GitHubWatchDaemon(store).run()
    except Exception:
        logging.exception("GitHub reply watcher stopped unexpectedly")
        status = 1
    raise SystemExit(status)


if __name__ == "__main__":
    main()
