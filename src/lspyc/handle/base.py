"""Abstract base class for LSP server handle."""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, TypeAlias

from .process import ProcessManager, ServerState
from .protocol import JsonRpcMessage, decode_message, encode_message

INBOUND_HANDLER: TypeAlias = Callable[["LspHandle", JsonRpcMessage], Awaitable[None]]


class LspHandle(ABC):
    """Abstract base class for LSP server handles.

    This class provides common JSON-RPC functionality for all LSP handles,
    regardless of the underlying transport mechanism (stdio, HTTP, WebSocket, etc.).
    Subclasses must implement transport-specific methods for starting, stopping,
    and sending raw data.
    """

    def __init__(
        self,
        request_handler: INBOUND_HANDLER | None = None,
        notification_handler: INBOUND_HANDLER | None = None,
    ) -> None:
        """Initialize the LSP handle."""
        self._next_id = 1
        self._pending_responses: dict[int | str, asyncio.Future[Any]] = {}
        self.request_handler: INBOUND_HANDLER | None = request_handler
        self.notification_handler: INBOUND_HANDLER | None = notification_handler

    @abstractmethod
    async def start(self) -> None:
        """Start the LSP server/connection.

        Raises:
            RuntimeError: If the server is already running
        """
        pass

    @abstractmethod
    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the LSP server/connection.

        Args:
            timeout: Maximum time to wait for graceful shutdown

        Raises:
            RuntimeError: If the server is not running
        """
        pass

    @abstractmethod
    async def _send_raw_message(self, message: dict[str, Any]) -> None:
        """Send a raw message through the transport layer.

        Args:
            message: The message to send

        Raises:
            RuntimeError: If the connection is not active
        """
        pass

    @property
    @abstractmethod
    def state(self) -> ServerState:
        """Get the current server state."""
        pass

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        pass

    async def send_request(
        self,
        method: str,
        params: Any = None,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response.

        Args:
            method: The method name
            params: The method parameters (optional)
            timeout: Maximum time to wait for response (optional)

        Returns:
            The result from the response

        Raises:
            RuntimeError: If the server is not running
            asyncio.TimeoutError: If the response times out
            Exception: If the server returns an error
        """
        if not self.is_running:
            raise RuntimeError("Server is not running")

        request_id = self._next_id
        self._next_id += 1

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        # Create future for response
        future: asyncio.Future[Any] = asyncio.Future()
        self._pending_responses[request_id] = future

        try:
            # Send request
            await self._send_raw_message(message)

            # Wait for response
            if timeout is not None:
                result = await asyncio.wait_for(future, timeout=timeout)
            else:
                result = await future

            return result

        except asyncio.TimeoutError:
            # Clean up on timeout
            self._pending_responses.pop(request_id, None)
            raise

    async def send_notification(self, method: str, params: Any = None) -> None:
        """Send a JSON-RPC notification (no response expected).

        Args:
            method: The method name
            params: The method parameters (optional)

        Raises:
            RuntimeError: If the server is not running
        """
        if not self.is_running:
            raise RuntimeError("Server is not running")

        message = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params

        await self._send_raw_message(message)

    async def send_response(
        self,
        request_id: int | str,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Send a JSON-RPC response to a request.

        Args:
            request_id: The ID of the request being responded to
            result: The result value (optional if error is provided)
            error: Error object (optional if result is provided)

        Raises:
            RuntimeError: If the server is not running
            ValueError: If both result and error are provided or neither is provided
        """
        if not self.is_running:
            raise RuntimeError("Server is not running")

        assert result is None or error is None

        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
        }

        if error is not None:
            message["error"] = error
        else:
            message["result"] = result

        await self._send_raw_message(message)

    async def _handle_message(self, message: JsonRpcMessage) -> None:
        """Handle an incoming message.

        Args:
            message: The decoded message
        """
        content = message.content

        # Handle response
        if message.is_response:
            request_id = content.get("id")
            if request_id in self._pending_responses:
                future = self._pending_responses.pop(request_id)

                if "error" in content:
                    error = content["error"]
                    future.set_exception(
                        Exception(f"LSP Error: {error.get('message', 'Unknown error')}")
                    )
                else:
                    future.set_result(content.get("result"))

        # Handle request
        elif message.is_request:
            if self.request_handler is not None:
                await self.request_handler(self, message)
                return
            request_id = content.get("id")
            if request_id is not None:
                await self.send_response(
                    request_id=request_id,
                    error={"code": -32603, "message": "Internal error"},
                )

        # Handle notification
        elif message.is_notification and self.notification_handler is not None:
            await self.notification_handler(self, message)

    def _cancel_pending_responses(self) -> None:
        """Cancel all pending responses."""
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()


class LspStdioHandle(LspHandle):
    """Base class for LSP servers communicating via stdio.

    The class handles process lifecycle, message encoding/decoding, and
    request/response correlation via the stdio transport.
    """

    def __init__(
        self,
        cmd: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Initialize the LSP server.

        Args:
            cmd: Command and arguments to launch the LSP server
            cwd: Working directory for the server process
            env: Environment variables for the server process
        """
        super().__init__()
        self._cmd = cmd
        self._process: ProcessManager | None = None
        self._cwd = cwd
        self._env = env
        self._buffer = b""

    async def start(self) -> None:
        """Start the LSP server process.

        Raises:
            RuntimeError: If the server is already running
            OSError: If the process fails to start
        """
        if self._process is not None and self._process.is_running:
            raise RuntimeError("Server is already running")

        self._process = ProcessManager(self._cmd, cwd=self._cwd, env=self._env)

        await self._process.start(
            on_stdout=self._on_stdout,
            on_stderr=self._on_stderr,
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the LSP server process.

        Args:
            timeout: Maximum time to wait for graceful shutdown

        Raises:
            RuntimeError: If the server is not running
        """
        if self._process is None:
            raise RuntimeError("Server is not running")

        # Cancel all pending responses
        self._cancel_pending_responses()

        await self._process.stop(timeout=timeout)

    async def _send_raw_message(self, message: dict[str, Any]) -> None:
        """Send a raw message through the stdio transport.

        Args:
            message: The message to send

        Raises:
            RuntimeError: If the server is not running
        """
        if self._process is None:
            raise RuntimeError("Server is not running")

        encoded = encode_message(message)
        await self._process.write(encoded)

    @property
    def state(self) -> ServerState:
        """Get the current server state."""
        if self._process is None:
            return ServerState.STOPPED
        return self._process.state

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._process is not None and self._process.is_running

    async def _on_stdout(self, data: bytes) -> None:
        """Handle data from server stdout.

        Args:
            data: Raw data from stdout
        """
        self._buffer += data

        # Try to decode messages from buffer
        while True:
            message, self._buffer = decode_message(self._buffer)
            if message is None:
                break

            await self._handle_message(message)

    async def _on_stderr(self, data: bytes) -> None:
        """Handle data from server stderr.

        Args:
            data: Raw data from stderr
        """
        # Default implementation does nothing with stderr
        # Subclasses can override to handle stderr output
        pass
