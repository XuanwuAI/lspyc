"""WebSocket transport for LSP servers."""

import asyncio
import logging
from typing import Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from .base import HandleUnavailableError, LspTransport

logger = logging.getLogger(__name__)


class WsTransport(LspTransport):
    """Transport that connects to a remote LSP server via WebSocket."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self._url = url
        self._headers = headers
        self._connect_timeout = connect_timeout
        self._websocket: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(
        self,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        if self._running:
            raise RuntimeError("Transport is already running")

        self._websocket = await asyncio.wait_for(
            websockets.connect(
                self._url,
                additional_headers=self._headers,
            ),
            timeout=self._connect_timeout,
        )
        self._running = True
        logger.info(f"WebSocket transport connected: {self._url}")
        self._reader_task = asyncio.create_task(
            self._read_messages(on_data, on_close)
        )

    async def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        self._running = False

        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None

        if self._websocket:
            try:
                await asyncio.wait_for(self._websocket.close(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            self._websocket = None
        logger.info(f"WebSocket transport stopped: {self._url}")

    async def write(self, data: bytes) -> None:
        if self._websocket is None:
            raise HandleUnavailableError("WebSocket transport is not connected")

        try:
            await self._websocket.send(data)
        except ConnectionClosed:
            raise HandleUnavailableError("WebSocket connection closed during send")

    # --- Internal ---

    async def _read_messages(
        self,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        assert self._websocket is not None
        try:
            while True:
                data = await self._websocket.recv()
                if isinstance(data, str):
                    data = data.encode("utf-8")
                await on_data(data)
        except ConnectionClosed:
            on_close()
        except asyncio.CancelledError:
            raise
