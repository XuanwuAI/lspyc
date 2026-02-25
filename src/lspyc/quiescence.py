"""Quiescence tracking for LSP servers."""

import asyncio
import time
from typing import Optional


class QuiescenceTracker:
    """Tracks LSP server activity to determine when it's quiescent.

    A server is considered quiescent when:
    1. All work done progress tokens have completed
    2. A grace period has elapsed since the last activity

    This is useful to avoid race conditions when opening files, as servers
    may need time to analyze files before responding to requests accurately.
    """

    def __init__(self, grace_period: float = 0.5) -> None:
        """Initialize the quiescence tracker.

        Args:
            grace_period: Time in seconds to wait after last activity before
                         considering the server quiescent (default: 0.5s)
        """
        self._active_tokens: set[str] = set()
        self._last_activity_time: Optional[float] = None
        self._grace_period = grace_period
        self._lock = asyncio.Lock()
        self._quiescent_event = asyncio.Event()
        self._quiescent_event.set()  # Initially quiescent
        self._grace_task: Optional[asyncio.Task] = None

    async def mark_work_started(self, token: str) -> None:
        """Mark that work has started for the given token.

        Called when receiving:
        - window/workDoneProgress/create request
        - $/progress notification with kind="begin"

        Args:
            token: The work done progress token
        """
        async with self._lock:
            self._active_tokens.add(token)
            self._last_activity_time = time.time()
            self._quiescent_event.clear()

            # Cancel any pending grace period task
            if self._grace_task and not self._grace_task.done():
                self._grace_task.cancel()
                self._grace_task = None

    async def mark_work_ended(self, token: str) -> None:
        """Mark that work has ended for the given token.

        Called when receiving:
        - $/progress notification with kind="end"

        Args:
            token: The work done progress token
        """
        async with self._lock:
            self._active_tokens.discard(token)
            self._last_activity_time = time.time()

            # If no more active work, start grace period
            if not self._active_tokens:
                # Cancel any existing grace task
                if self._grace_task and not self._grace_task.done():
                    self._grace_task.cancel()

                # Start new grace period task
                self._grace_task = asyncio.create_task(self._grace_period_task())

    async def wait_for_quiescence(self, timeout: float = 10.0) -> bool:
        """Wait until the server becomes quiescent.

        Args:
            timeout: Maximum time to wait in seconds (default: 10.0)

        Returns:
            True if server became quiescent, False if timeout occurred
        """
        try:
            # First wait for grace period to ensure we're not in a race
            await asyncio.sleep(self._grace_period)
            await asyncio.wait_for(self._quiescent_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _grace_period_task(self) -> None:
        """Background task that waits for the grace period and then sets quiescent."""
        try:
            await asyncio.sleep(self._grace_period)

            # After grace period, check if still no active work
            async with self._lock:
                if not self._active_tokens:
                    self._quiescent_event.set()
        except asyncio.CancelledError:
            # Task was cancelled, which is normal
            pass

    async def close(self) -> None:
        """Clean up resources."""
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
            await self._grace_task
