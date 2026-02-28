import asyncio
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from .base import LspHandle
from .process import ServerState
from .protocol import decode_message, encode_message


class LspWsHandle(LspHandle):
    """LSP handle for WebSocket connections with auto-reconnect.

    This class connects to a remote LSP server via WebSocket and handles
    message encoding/decoding using Content-Length headers (same as stdio).
    It supports automatic reconnection on unexpected disconnects.
    """

    def __init__(
        self,
        workspace_root: str,
        url: str,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 10.0,
        reconnect_delay: float = 2.0,
        max_reconnect_attempts: int = -1,
    ) -> None:
        """Initialize the WebSocket LSP handle.

        Args:
            url: WebSocket URL (ws:// or wss://)
            headers: Optional headers for WebSocket connection
            connect_timeout: Connection timeout in seconds
            reconnect_delay: Delay between reconnection attempts in seconds
            max_reconnect_attempts: Maximum reconnection attempts (-1 for infinite)
            workspace_root: (remote) workspace root for initialization
        """
        super().__init__(workspace_root=workspace_root)
        self._url = url
        self._headers = headers
        self._connect_timeout = connect_timeout
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_attempts = max_reconnect_attempts

        self._websocket: ClientConnection | None = None
        self._state = ServerState.STOPPED
        self._buffer = b""
        self._receive_task: asyncio.Task[None] | None = None
        self._should_reconnect = False
        self._reconnect_attempts = 0

    async def start(self) -> dict[str, Any] | None:
        """Establish WebSocket connection and start receive loop.

        Returns:
            Server capabilities if auto-initialized, None otherwise

        Raises:
            RuntimeError: If already connected
            OSError: If connection fails
        """
        assert self._state == ServerState.STOPPED
        self._state = ServerState.WAITING
        self._should_reconnect = True
        self._reconnect_attempts = 0
        try:
            await self._connect()
            self._state = ServerState.RUNNING
        except Exception:
            self._state = ServerState.STOPPED
            raise
        assert self._receive_task is None
        self._receive_task = asyncio.create_task(self._receive_loop())

        capabilities = await self.initialize(workspace_root=self._workspace_root)
        self._server_capabilities = capabilities
        return capabilities

    async def stop(self, timeout: float = 5.0) -> None:
        """Close WebSocket connection gracefully."""
        if self._state == ServerState.STOPPED:
            return
        self._should_reconnect = False
        self._state = ServerState.STOPPED
        assert self._receive_task is not None
        if self._receive_task:
            self._receive_task.cancel()
            await asyncio.gather(self._receive_task, return_exceptions=True)
        self._cancel_pending_responses()

    async def _send_raw_message(self, message: dict[str, Any]) -> None:
        """Send a raw message through WebSocket with Content-Length header.

        Args:
            message: The message to send

        Raises:
            RuntimeError: If not connected
        """
        if self._websocket is None or self._state != ServerState.RUNNING:
            raise RuntimeError("WebSocket is not connected")

        # Encode message with Content-Length header
        encoded = encode_message(message)

        try:
            await self._websocket.send(encoded)
        except ConnectionClosed as e:
            raise RuntimeError("WebSocket connection closed") from e

    @property
    def state(self) -> ServerState:
        """Get the current connection state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if the handle is currently running."""
        return self._state == ServerState.RUNNING

    async def _connect(self) -> None:
        """Establish WebSocket connection (internal)."""

        self._websocket = await asyncio.wait_for(
            websockets.connect(
                self._url,
                additional_headers=self._headers,
            ),
            timeout=self._connect_timeout,
        )

        self._reconnect_attempts = 0
        self._buffer = b""

    async def _disconnect(self, timeout: float = 5.0) -> None:
        """Disconnect WebSocket (internal)."""
        if not self._websocket:
            return
        try:
            await asyncio.wait_for(self._websocket.close(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._websocket = None

    async def _receive_loop(self) -> None:
        """Continuously receive and process messages from WebSocket."""
        while True:
            if self._websocket is None:
                # TODO: remove this branch
                await asyncio.sleep(1)
                continue
            try:
                data = await self._websocket.recv()
                if isinstance(data, str):
                    data = data.encode("utf-8")
                self._buffer += data
                while True:
                    message, self._buffer = decode_message(self._buffer)
                    if message is None:
                        break
                    await self._handle_message(message)
            except ConnectionClosed:
                await self._handle_disconnect()
            except asyncio.CancelledError:
                await self._disconnect()
                raise
            except Exception:
                # TODO: handle unexpected errors
                raise

    async def _handle_disconnect(self) -> None:
        """Handle disconnection and attempt reconnection if appropriate."""
        while self._should_reconnect:
            self._state = ServerState.WAITING
            await asyncio.sleep(self._reconnect_delay)
            try:
                await self._connect()
                self._state = ServerState.RUNNING
                await self.initialize(workspace_root=self._workspace_root)
                return
            except Exception:
                self._reconnect_attempts += 1
                if (
                    self._max_reconnect_attempts >= 0
                    and self._reconnect_attempts >= self._max_reconnect_attempts
                ):
                    self._state = ServerState.STOPPED
                    self._should_reconnect = False
        await self._disconnect()
