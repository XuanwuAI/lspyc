import asyncio
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from .base import LspHandle
from .process import ServerState
from .protocol import decode_message, encode_message


class LspWsHandle(LspHandle):
    """LSP handle for WebSocket connections.

    Connects to a remote LSP server via WebSocket and handles message
    encoding/decoding using Content-Length headers (same as stdio).
    Reconnection is NOT handled here — the client layer is responsible
    for restarting handles on connection failure.
    """

    def __init__(
        self,
        workspace_root: str,
        url: str,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        """Initialize the WebSocket LSP handle.

        Args:
            workspace_root: Workspace root for initialization
            url: WebSocket URL (ws:// or wss://)
            headers: Optional headers for WebSocket connection
            connect_timeout: Connection timeout in seconds
        """
        super().__init__(workspace_root=workspace_root)
        self._url = url
        self._headers = headers
        self._connect_timeout = connect_timeout

        self._websocket: ClientConnection | None = None
        self._state = ServerState.STOPPED
        self._buffer = b""
        self._reader_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> ServerState:
        """Get the current connection state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if the handle is currently running."""
        return self._state == ServerState.RUNNING

    async def start(self) -> None:
        """Establish WebSocket connection and start receive loop.

        Raises:
            RuntimeError: If already running
            OSError: If connection fails
        """
        assert self._state == ServerState.STOPPED
        self._websocket = await asyncio.wait_for(
            websockets.connect(
                self._url,
                additional_headers=self._headers,
            ),
            timeout=self._connect_timeout,
        )
        self._buffer = b""
        self._state = ServerState.RUNNING
        self._reader_task = asyncio.create_task(self._read_messages())

    async def stop(self, timeout: float = 5.0) -> None:
        """Close WebSocket connection gracefully."""
        if self._state == ServerState.STOPPED:
            return
        self._state = ServerState.STOPPED

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

        self._cancel_pending_responses()

    async def _send_raw_message(self, message: dict[str, Any]) -> None:
        """Send a raw message through WebSocket.

        Args:
            message: The message to send

        Raises:
            ConnectionError: If not connected
            ConnectionClosed: If the connection is lost during send
        """
        if self._websocket is None or self._state != ServerState.RUNNING:
            raise ConnectionError("WebSocket is not connected")

        encoded = encode_message(message)
        await self._websocket.send(encoded)

    async def _read_messages(self) -> None:
        """Read and dispatch messages from websocket until closed."""
        assert self._websocket is not None
        try:
            while True:
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
            self._cancel_pending_responses()
