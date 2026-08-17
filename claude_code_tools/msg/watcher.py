"""Async watcher daemon for msg delivery notifications.

Monitors the SQLite DB for pending deliveries and
delivers notifications to recipient agents.

Delivery logic for headed agents:
- Busy (not idle) → release claim, Stop hook handles it
- Idle + prompt empty → type slash command into pane
- Idle + user typing → release claim, UserPromptSubmit
  hook handles it
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
from collections import defaultdict

from .models import _new_uuid
from .prompt_detect import PromptState, detect_prompt_state
from .store import MsgStore, DEFAULT_DB_PATH

logger = logging.getLogger("msg.watcher")

POLL_INTERVAL = 2.0  # seconds between DB checks
IDLE_CHECK_TIMEOUT = 3.0  # quick idle check (not blocking)
IDLE_CLEANUP_TIMEOUT = 5
IDLE_TIME = 2.0  # seconds of no output = idle
HEARTBEAT_INTERVAL = 10.0  # seconds between heartbeats
SEND_TIMEOUT = 30
SEND_CLEANUP_TIMEOUT = 5
SEND_MAX_SECS = SEND_TIMEOUT + SEND_CLEANUP_TIMEOUT
SEND_LEASE_SECS = SEND_MAX_SECS + 5


class Watcher:
    """Async delivery watcher daemon."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
    ) -> None:
        self.store = MsgStore(db_path)
        self.watcher_id = _new_uuid()
        self.pid = os.getpid()
        self._running = True

    async def run(self) -> None:
        """Main watcher loop."""
        logger.info(
            "Watcher started (id=%s, pid=%d)",
            self.watcher_id[:8], self.pid,
        )

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig, self._handle_shutdown,
            )

        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop()
        )

        try:
            while self._running:
                await self._process_pending()
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.info("Watcher stopped.")

    def _handle_shutdown(self) -> None:
        logger.info("Shutdown signal received.")
        self._running = False

    async def _heartbeat_loop(self) -> None:
        """Periodically update heartbeat in DB."""
        while True:
            try:
                self.store.update_heartbeat(
                    self.watcher_id, self.pid,
                )
            except Exception as e:
                logger.warning("Heartbeat failed: %s", e)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _process_pending(self) -> None:
        """Claim and process pending deliveries."""
        try:
            released = self.store.release_expired_claims()
            if released:
                logger.debug(
                    "Released %d expired claims", released,
                )

            claimed = self.store.claim_pending_deliveries(
                self.watcher_id,
            )
            if not claimed:
                return

            by_recipient: dict[str, list[dict]] = (
                defaultdict(list)
            )
            for d in claimed:
                by_recipient[d["recipient_id"]].append(d)

            tasks = []
            for recipient_id, deliveries in (
                by_recipient.items()
            ):
                tasks.append(
                    self._deliver_to_recipient(
                        recipient_id, deliveries,
                    )
                )

            if tasks:
                await asyncio.gather(
                    *tasks, return_exceptions=True,
                )

        except Exception as e:
            logger.error("Error processing pending: %s", e)

    async def _deliver_to_recipient(
        self,
        recipient_id: str,
        deliveries: list[dict],
    ) -> None:
        """Deliver notifications to a single recipient."""
        try:
            deliveries = self._current_deliveries(deliveries)
            if not deliveries:
                return

            recipient_name = deliveries[0]["recipient_name"]
            pane_id = deliveries[0]["recipient_pane_id"]
            tmux_socket = deliveries[0].get("recipient_tmux_socket")
            agent_kind = deliveries[0].get(
                "recipient_agent_kind", "claude",
            )
            target = pane_id

            # Step 1: Quick idle check (non-blocking)
            is_idle = await self._check_idle(target, tmux_socket)

            if not is_idle:
                # Agent is busy — release claims.
                # Stop hook will handle delivery.
                logger.debug(
                    "%s is busy, releasing claims.",
                    recipient_name,
                )
                self._release_deliveries(deliveries)
                return

            # Step 2: Check prompt state
            prompt_state = detect_prompt_state(
                target, agent_kind, tmux_socket,
            )

            if prompt_state == PromptState.HAS_TEXT:
                # User is typing — release claims.
                # UserPromptSubmit hook will handle it.
                logger.debug(
                    "%s has text in prompt, releasing.",
                    recipient_name,
                )
                self._release_deliveries(deliveries)
                return

            if prompt_state == PromptState.UNKNOWN:
                # Can't determine — release, retry next
                # loop iteration.
                logger.debug(
                    "%s prompt state unknown, releasing.",
                    recipient_name,
                )
                self._release_deliveries(deliveries)
                return

            # Step 3: Prompt is empty — safe to inject
            notification = self._build_notification(
                agent_kind,
            )

            logger.info(
                "Delivering to %s (%s): %s",
                recipient_name, target, notification,
            )

            deliveries = self._current_deliveries(deliveries)
            if not deliveries:
                return
            delivery_ids = [delivery["id"] for delivery in deliveries]
            if not self.store.renew_deliveries(
                delivery_ids, self.watcher_id, SEND_LEASE_SECS,
            ):
                self._release_deliveries(deliveries)
                return
            await self._tmux_send(target, notification, tmux_socket)

            for d in deliveries:
                self.store.mark_notified(d["id"], self.watcher_id)

            logger.info(
                "Notified %s successfully.",
                recipient_name,
            )

        except Exception as e:
            logger.error(
                "Failed to deliver to %s: %s",
                recipient_id[:8], e,
            )
            for d in deliveries:
                self.store.mark_delivery_failed(
                    d["id"], self.watcher_id, error=str(e),
                )
    def _current_deliveries(self, deliveries: list[dict]) -> list[dict]:
        current = []
        for delivery in deliveries:
            if self.store.claim_is_current(delivery["id"], self.watcher_id):
                current.append(delivery)
            else:
                self.store.release_delivery(delivery["id"], self.watcher_id)
        return current

    def _release_deliveries(
        self, deliveries: list[dict],
    ) -> None:
        """Release claimed deliveries back to pending."""
        for d in deliveries:
            self.store.release_delivery(d["id"], self.watcher_id)

    def _build_notification(
        self, agent_kind: str,
    ) -> str:
        """Build the client-specific inbox prompt."""
        if agent_kind == "codex":
            return "You have a new inter-agent message. Run msg inbox now."
        return "/msg:inbox"

    async def _check_idle(
        self, pane_target: str, tmux_socket: str | None,
    ) -> bool:
        """Quick non-blocking idle check."""
        if not tmux_socket:
            return False
        try:
            env = os.environ.copy()
            env["TMUX"] = f"{tmux_socket},0,0"
            proc = await asyncio.create_subprocess_exec(
                "tmux-cli", "wait_idle",
                f"--pane={pane_target}",
                f"--idle-time={IDLE_TIME}",
                f"--timeout={IDLE_CHECK_TIMEOUT}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                await asyncio.wait_for(
                    proc.communicate(),
                    timeout=IDLE_CHECK_TIMEOUT + IDLE_CLEANUP_TIMEOUT,
                )
            except BaseException as exc:
                if proc.returncode is not None:
                    raise
                proc.kill()
                try:
                    await asyncio.wait_for(
                        proc.communicate(), timeout=IDLE_CLEANUP_TIMEOUT,
                    )
                except TimeoutError:
                    logger.error("Timed out reaping tmux-cli idle process")
                if not isinstance(exc, Exception):
                    raise
                return False
            return proc.returncode == 0
        except Exception:
            return False

    async def _tmux_send(
        self,
        pane_target: str,
        text: str,
        tmux_socket: str | None,
    ) -> None:
        """Type text into a pane through tmux-cli on its registered server."""
        if not tmux_socket:
            raise RuntimeError("recipient tmux socket is missing")
        env = os.environ.copy()
        env["TMUX"] = f"{tmux_socket},0,0"
        proc = None
        try:
            async with asyncio.timeout(SEND_TIMEOUT):
                proc = await asyncio.create_subprocess_exec(
                    "tmux-cli", "send", text,
                    f"--pane={pane_target}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                _, stderr = await proc.communicate()
        except BaseException:
            if proc is not None and proc.returncode is None:
                proc.kill()
                try:
                    await asyncio.wait_for(
                        proc.communicate(), timeout=SEND_CLEANUP_TIMEOUT,
                    )
                except TimeoutError:
                    logger.error("Timed out reaping tmux-cli send process")
            raise
        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else ""
            raise RuntimeError(
                f"tmux-cli send failed for {pane_target}: {err}"
            )


def run_watcher(db_path: str = DEFAULT_DB_PATH) -> None:
    """Entry point for the watcher daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s "
        "%(message)s",
        datefmt="%H:%M:%S",
    )
    lock = open(f"{db_path}.watcher.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        logger.info("Watcher already running for %s", db_path)
        return
    with lock:
        watcher = Watcher(db_path=db_path)
        asyncio.run(watcher.run())
