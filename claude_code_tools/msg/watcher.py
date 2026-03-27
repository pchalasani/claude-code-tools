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
IDLE_TIME = 2.0  # seconds of no output = idle
HEARTBEAT_INTERVAL = 10.0  # seconds between heartbeats


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
        self._active_recipients: set[str] = set()

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
                if recipient_id in self._active_recipients:
                    continue
                self._active_recipients.add(recipient_id)
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
            if not deliveries:
                return

            recipient_name = deliveries[0]["recipient_name"]
            pane_id = deliveries[0]["recipient_pane_id"]
            display_addr = (
                deliveries[0]["recipient_display_addr"]
            )
            agent_kind = deliveries[0].get(
                "recipient_agent_kind", "claude",
            )
            target = display_addr or pane_id

            # Step 1: Quick idle check (non-blocking)
            is_idle = await self._check_idle(target)

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
                target, agent_kind,
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

            await self._tmux_send(target, notification)

            for d in deliveries:
                self.store.mark_notified(d["id"])

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
                    d["id"], error=str(e),
                )
        finally:
            self._active_recipients.discard(recipient_id)

    def _release_deliveries(
        self, deliveries: list[dict],
    ) -> None:
        """Release claimed deliveries back to pending."""
        for d in deliveries:
            self.store.mark_delivery_failed(
                d["id"],
                error="Released by watcher (not ready)",
            )

    def _build_notification(
        self, agent_kind: str,
    ) -> str:
        """Build notification slash command."""
        if agent_kind == "codex":
            return "/prompts:inbox"
        return "/msg:inbox"

    async def _check_idle(
        self, pane_target: str,
    ) -> bool:
        """Quick non-blocking idle check."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux-cli", "wait_idle",
                f"--pane={pane_target}",
                f"--idle-time={IDLE_TIME}",
                f"--timeout={IDLE_CHECK_TIMEOUT}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=IDLE_CHECK_TIMEOUT + 5,
            )
            return proc.returncode == 0
        except (asyncio.TimeoutError, Exception):
            return False

    async def _tmux_send(
        self,
        pane_target: str,
        text: str,
    ) -> None:
        """Type text into a tmux pane via tmux-cli."""
        proc = await asyncio.create_subprocess_exec(
            "tmux-cli", "send", text,
            f"--pane={pane_target}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=30,
        )
        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else ""
            raise RuntimeError(
                f"tmux-cli send failed for "
                f"{pane_target}: {err}"
            )


def run_watcher(db_path: str = DEFAULT_DB_PATH) -> None:
    """Entry point for the watcher daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s "
        "%(message)s",
        datefmt="%H:%M:%S",
    )
    watcher = Watcher(db_path=db_path)
    asyncio.run(watcher.run())
