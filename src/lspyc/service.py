"""WebSocket server for exposing LSP servers over WebSocket.

This module provides a WebSocket server that can expose multiple LSP server types,
with each client connection spawning a dedicated LSP server process.
"""

import asyncio
import http
import logging
import os
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

LOG_LEVEL = os.getenv("LSPYC_SERVICE_LOG_LEVEL", "INFO")
HOST = os.getenv("LSPYC_SERVICE_HOST", "localhost")
PORT = int(os.getenv("LSPYC_SERVICE_PORT", "8080"))

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# Add console handler if no handlers are configured
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(LOG_LEVEL)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class ClientConnection:
    """Manages a single WebSocket client connection and its LSP server process.

    This class uses asyncio.create_subprocess_exec to manage the LSP process
    and creates concurrent tasks for bidirectional byte forwarding.
    """

    def __init__(
        self,
        websocket: ServerConnection,
        lsp_command: list[str],
    ) -> None:
        """Initialize a client connection.

        Args:
            websocket: The WebSocket connection to the client
            lsp_command: Command to launch the LSP server
            cwd: Working directory for the LSP server process
            env: Environment variables for the LSP server process
        """
        self.websocket = websocket
        self.lsp_command = lsp_command
        self.process: asyncio.subprocess.Process | None = None
        self.client_id = id(websocket)
        self._running = False
        self._tasks: list[asyncio.Task] = []

        logger.info(f"[Client {self.client_id}] Connection created")

    async def start(self) -> None:
        """Start the LSP server process and begin bidirectional forwarding."""
        self._running = True
        logger.info(
            f"[Client {self.client_id}] Starting LSP server: {' '.join(self.lsp_command)}"
        )

        try:
            # Start LSP server process using asyncio directly
            self.process = await asyncio.create_subprocess_exec(
                *self.lsp_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            logger.info(
                f"[Client {self.client_id}] LSP server started (PID: {self.process.pid})"
            )

            # Create concurrent tasks for bidirectional forwarding
            self._tasks = [
                asyncio.create_task(
                    self._ws_to_stdin(), name=f"ws_to_stdin_{self.client_id}"
                ),
                asyncio.create_task(
                    self._stdout_to_ws(), name=f"stdout_to_ws_{self.client_id}"
                ),
                asyncio.create_task(
                    self._stderr_logger(), name=f"stderr_logger_{self.client_id}"
                ),
            ]

            # Wait for any task to complete (usually means disconnection)
            done, pending = await asyncio.wait(
                self._tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()

            # Wait for cancelled tasks to finish
            await asyncio.gather(*pending, return_exceptions=True)

        except Exception as e:
            logger.error(f"[Client {self.client_id}] Error: {e}", exc_info=True)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the client connection and clean up resources."""
        if not self._running:
            return

        self._running = False
        logger.info(f"[Client {self.client_id}] Stopping")

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to finish
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Terminate process
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info(f"[Client {self.client_id}] Process terminated")
            except asyncio.TimeoutError:
                logger.warning(
                    f"[Client {self.client_id}] Process didn't terminate, killing"
                )
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.warning(f"[Client {self.client_id}] Error stopping process: {e}")

        # Close WebSocket
        try:
            await self.websocket.close()
        except Exception:
            pass

        logger.info(f"[Client {self.client_id}] Stopped")

    async def _ws_to_stdin(self) -> None:
        """Forward bytes from WebSocket to process stdin."""
        try:
            while self._running:
                # Receive data from WebSocket
                data = await self.websocket.recv(decode=False)
                # Write to process stdin
                if self.process and self.process.stdin:
                    self.process.stdin.write(data)
                    await self.process.stdin.drain()
                    logger.debug(
                        f"[Client {self.client_id}] → LSP: {len(data)} bytes (data: {data})"
                    )

        except ConnectionClosed:
            logger.info(f"[Client {self.client_id}] WebSocket closed")
        except asyncio.CancelledError:
            logger.debug(f"[Client {self.client_id}] ws_to_stdin task cancelled")
            raise
        except Exception as e:
            logger.error(
                f"[Client {self.client_id}] Error in ws_to_stdin: {e}", exc_info=True
            )
        finally:
            # Close stdin to signal process
            if self.process and self.process.stdin:
                try:
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()
                except Exception:
                    pass

    async def _stdout_to_ws(self) -> None:
        """Forward bytes from process stdout to WebSocket."""
        try:
            while self._running:
                # Read from process stdout
                if self.process and self.process.stdout:
                    data = await self.process.stdout.read(8192)

                    if not data:  # EOF
                        logger.info(f"[Client {self.client_id}] LSP stdout closed")
                        break

                    # Forward to WebSocket
                    await self.websocket.send(data)
                    logger.debug(
                        f"[Client {self.client_id}] ← LSP: {len(data)} bytes (data: {data})"
                    )
                else:
                    break

        except ConnectionClosed:
            logger.info(f"[Client {self.client_id}] WebSocket closed while sending")
        except asyncio.CancelledError:
            logger.debug(f"[Client {self.client_id}] stdout_to_ws task cancelled")
            raise
        except Exception as e:
            logger.error(
                f"[Client {self.client_id}] Error in stdout_to_ws: {e}", exc_info=True
            )

    async def _stderr_logger(self) -> None:
        """Log stderr output from the LSP process."""
        try:
            while self._running:
                # Read from process stderr
                if self.process and self.process.stderr:
                    data = await self.process.stderr.read(8192)

                    if not data:  # EOF
                        break

                    # Log stderr
                    stderr_text = data.decode("utf-8", errors="replace").strip()
                    if stderr_text:
                        logger.debug(
                            f"[Client {self.client_id}] LSP stderr: {stderr_text}"
                        )
                else:
                    break

        except asyncio.CancelledError:
            logger.debug(f"[Client {self.client_id}] stderr_logger task cancelled")
            raise
        except Exception as e:
            logger.error(f"[Client {self.client_id}] Error in stderr_logger: {e}")


class LspService:
    """WebSocket server that exposes multiple LSP server types.

    This server accepts WebSocket connections and routes them to appropriate
    LSP server processes based on the URL path. Each client gets a dedicated
    LSP server instance.
    """

    def __init__(
        self,
        lsp_configs: dict[str, dict[str, Any]],
        host: str = "localhost",
        port: int = 8080,
    ) -> None:
        """Initialize the WebSocket LSP server.

        Args:
            lsp_configs: Dictionary mapping LSP names to their configurations.
                Each config should have:
                - "command": list[str] - Command to launch the LSP server
                - "cwd": str | None - Working directory (optional)
                - "env": dict[str, str] | None - Environment variables (optional)
            host: Host to bind the server to
            port: Port to bind the server to

        Example:
            lsp_configs = {
                "pyright": {
                    "command": ["pyright-langserver", "--stdio"],
                },
                "clangd": {
                    "command": ["clangd"],
                },
            }
        """
        self.lsp_configs = lsp_configs
        self.host = host
        self.port = port
        self._clients: set[ClientConnection] = set()
        self._server = None

        logger.info(f"Server configured with LSP types: {list(lsp_configs.keys())}")

    async def start(self) -> None:
        """Start the WebSocket server.

        This method starts the server and blocks until stopped or an error occurs.
        """
        logger.info(f"Starting WebSocket LSP server on {self.host}:{self.port}")

        try:
            async with serve(
                self._handle_client,
                self.host,
                self.port,
                process_request=self._process_request,
            ) as server:
                self._server = server
                logger.info(
                    f"WebSocket LSP server listening on ws://{self.host}:{self.port}"
                )
                logger.info(
                    f"Available LSP paths: {[f'/{name}' for name in self.lsp_configs.keys()]}"
                )

                # Keep server running indefinitely
                await asyncio.Future()

        except asyncio.CancelledError:
            logger.info("Server cancelled")
            raise
        except Exception as e:
            logger.error(f"Server error: {e}", exc_info=True)
            raise
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the WebSocket server and clean up all connections."""
        logger.info("Stopping WebSocket LSP server")
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up all active client connections."""
        if self._clients:
            logger.info(f"Cleaning up {len(self._clients)} active connections")
            cleanup_tasks = [client.stop() for client in list(self._clients)]
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            self._clients.clear()

    async def _process_request(
        self,
        connection: ServerConnection,
        request,
    ) -> Response | None:
        """Process WebSocket upgrade request and validate LSP type.

        Args:
            connection: The WebSocket connection
            request: The HTTP request

        Returns:
            None to accept the connection, or Response to reject
        """
        # Extract LSP name from path
        path = request.path
        lsp_name = path.strip("/")

        if not lsp_name:
            logger.warning("Connection rejected: No LSP type specified in path")
            return Response(
                status_code=http.HTTPStatus.BAD_REQUEST,
                reason_phrase="Bad Request",
                headers=Headers(),
                body=b"LSP type must be specified in path (e.g., /pyright)\n",
            )

        if lsp_name not in self.lsp_configs:
            logger.warning(f"Connection rejected: Unknown LSP type '{lsp_name}'")
            available = ", ".join(self.lsp_configs.keys())
            return Response(
                status_code=http.HTTPStatus.NOT_FOUND,
                reason_phrase="Not Found",
                headers=Headers(),
                body=f"Unknown LSP type '{lsp_name}'. Available: {available}\n".encode(),
            )

        # Accept the connection
        logger.debug(f"Accepting connection for LSP type: {lsp_name}")
        return None

    async def _handle_client(self, websocket: ServerConnection) -> None:
        """Handle a new client connection.

        Args:
            websocket: The WebSocket connection from the client
        """
        # Extract LSP name from path
        if not websocket.request:
            logger.error("WebSocket connection has no request")
            return

        path = websocket.request.path
        lsp_name = path.strip("/")

        client_address = websocket.remote_address
        logger.info(f"New client connection from {client_address} for LSP: {lsp_name}")

        # Get LSP configuration
        lsp_config = self.lsp_configs[lsp_name]

        # Create client connection
        client = ClientConnection(
            websocket=websocket,
            lsp_command=lsp_config["command"],
        )

        self._clients.add(client)

        try:
            # Start message processing
            await client.start()

        except Exception as e:
            logger.error(f"Error handling client {client_address}: {e}", exc_info=True)

        finally:
            # Clean up
            self._clients.discard(client)
            logger.info(f"Client {client_address} disconnected")

    @property
    def active_connections(self) -> int:
        """Get the number of active client connections."""
        return len(self._clients)


async def run_server(
    lsp_configs: dict[str, dict[str, Any]],
    host: str = HOST,
    port: int = PORT,
) -> None:
    """Convenience function to run a WebSocket LSP server.

    Args:
        lsp_configs: Dictionary mapping LSP names to configurations
        host: Host to bind the server to
        port: Port to bind the server to

    Example:
        lsp_configs = {
            "pyright": {
                "command": ["pyright-langserver", "--stdio"],
            },
            "clangd": {
                "command": ["clangd"],
            },
        }
        await run_server(lsp_configs)
    """
    server = LspService(
        lsp_configs=lsp_configs,
        host=host,
        port=port,
    )

    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await server.stop()


if __name__ == "__main__":

    lsp_configs = {
        "pyright-langserver": {
            "command": ["pyright-langserver", "--stdio"],
        },
        "clangd": {
            "command": ["clangd", "--background-index"],
        },
        "rust-analyzer": {
            "command": ["rust-analyzer"],
        },
        "typescript-language-server": {
            "command": ["typescript-language-server", "--stdio"],
        },
        "gopls": {
            "command": ["gopls"],
        },
        "jdtls": {
            "command": ["jdtls"],
        },
        "kotlin-language-server": {
            "command": ["kotlin-language-server"],
        },
    }

    asyncio.run(run_server(lsp_configs))
