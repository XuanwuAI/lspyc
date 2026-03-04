"""LSP handle with transport abstraction."""

import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeAlias

from .protocol import JsonRpcMessage, decode_message, encode_message

INBOUND_HANDLER: TypeAlias = Callable[["LspHandle", JsonRpcMessage], Awaitable[None]]


class HandleUnavailableError(Exception):
    """Raised when an LSP handle's connection is lost or unusable.

    The client should discard this handle and create a new one.
    """


class LspTransport(ABC):
    """Abstract byte-level I/O for an LSP server connection."""

    @abstractmethod
    async def start(
        self,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        """Start the transport.

        Args:
            on_data: Called when bytes arrive from the server.
            on_close: Called when the connection drops unexpectedly.
        """

    @abstractmethod
    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the transport gracefully.

        Args:
            timeout: Maximum time to wait for graceful shutdown.
        """

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Write bytes to the server.

        Raises:
            HandleUnavailableError: If the transport is dead.
        """


class LspHandle:
    """Concrete LSP handle owning JSON-RPC protocol logic.

    Delegates byte-level I/O to an ``LspTransport``. Owns the Content-Length
    deframing buffer, request/response correlation, and the LSP
    initialize/initialized handshake.
    """

    def __init__(
        self,
        workspace_root: str,
        transport: LspTransport,
        request_handler: INBOUND_HANDLER | None = None,
        notification_handler: INBOUND_HANDLER | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._transport = transport
        self._server_capabilities: dict[str, Any] | None = None
        self._next_id = 1
        self._pending_responses: dict[int | str, asyncio.Future[Any]] = {}
        self.request_handler: INBOUND_HANDLER | None = request_handler
        self.notification_handler: INBOUND_HANDLER | None = notification_handler
        self._buffer = b""

    # --- URI helpers ---

    def build_uri(self, relative_path: str) -> str:
        assert not relative_path.startswith("/") or relative_path.startswith("..")
        abs_path = os.path.join(self._workspace_root, relative_path)
        return Path(abs_path).as_uri()

    def uri2rel(self, uri: str) -> str:
        """Convert a URI to a relative path from the workspace root."""
        path = Path(uri.replace("file://", ""))
        workspace = Path(self._workspace_root)
        return str(path.relative_to(workspace))

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the underlying transport."""
        self._buffer = b""
        await self._transport.start(
            on_data=self._on_data,
            on_close=self._on_close,
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the underlying transport and cancel pending requests."""
        self._cancel_pending_responses()
        await self._transport.stop(timeout=timeout)

    # --- Sending ---

    async def send_request(
        self,
        method: str,
        params: Any = None,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        assert self._transport

        request_id = self._next_id
        self._next_id += 1

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        future: asyncio.Future[Any] = asyncio.Future()
        self._pending_responses[request_id] = future

        try:
            await self._send_raw_message(message)

            if timeout is not None:
                result = await asyncio.wait_for(future, timeout=timeout)
            else:
                result = await future

            return result

        except (asyncio.TimeoutError, HandleUnavailableError):
            self._pending_responses.pop(request_id, None)
            raise

    async def send_notification(self, method: str, params: Any = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        assert self._transport

        message: dict[str, Any] = {
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
        """Send a JSON-RPC response to a server request."""
        assert self._transport
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

    # --- LSP handshake ---

    async def initialize(
        self,
        workspace_root: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Perform the LSP initialize/initialized handshake."""
        assert self._transport

        root_uri = Path(workspace_root).as_uri()

        init_params = {
            "processId": None,
            "rootUri": root_uri,
            "rootPath": workspace_root,
            "capabilities": {
                "textDocument": {
                    "definition": {},
                    "references": {},
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                    },
                },
                "window": {"workDoneProgress": True},
            },
        }

        result = await self.send_request(
            method="initialize",
            params=init_params,
            timeout=timeout,
        )

        await self.send_notification(method="initialized", params={})
        return result if result else {}

    # --- Internal ---

    async def _send_raw_message(self, message: dict[str, Any]) -> None:
        encoded = encode_message(message)
        await self._transport.write(encoded)

    async def _on_data(self, data: bytes) -> None:
        """Called by the transport when bytes arrive."""
        self._buffer += data

        while True:
            message, self._buffer = decode_message(self._buffer)
            if message is None:
                break
            await self._handle_message(message)

    def _on_close(self) -> None:
        """Called by the transport when the connection drops."""
        self._cancel_pending_responses()

    async def _handle_message(self, message: JsonRpcMessage) -> None:
        content = message.content

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

        elif message.is_notification and self.notification_handler is not None:
            await self.notification_handler(self, message)

    def _cancel_pending_responses(self) -> None:
        """Cancel all pending responses with HandleUnavailableError."""
        for future in self._pending_responses.values():
            if not future.done():
                future.set_exception(HandleUnavailableError("LSP connection lost"))
        self._pending_responses.clear()
