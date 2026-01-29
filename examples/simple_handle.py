"""Example of a concrete LSP server implementation.

This example shows how to subclass LspStdioServer to create a specific
LSP server implementation (e.g., pyright, rust-analyzer, etc.).
"""

import asyncio
import os
import pathlib

from lspyc import LspStdioHandle
from lspyc.handle.protocol import JsonRpcMessage

THIS_FILE = __file__
PROJ_DIR = os.path.dirname(os.path.dirname(THIS_FILE))


class PyrightHandle(LspStdioHandle):
    """Example implementation for the Pyright language server handle."""

    def get_launch_command(self) -> list[str]:
        """Return the command to launch Pyright LSP server."""
        return ["pyright-langserver", "--stdio"]


async def example_message_handler(message: JsonRpcMessage) -> None:
    """Example handler for incoming server messages."""
    content = message.content

    if message.is_notification:
        print(f"Received notification: {content}")
    elif message.is_request:
        print(f"Received request: {content}")


async def main() -> None:
    """Main example demonstrating LSP server usage."""
    # Create server instance
    server = PyrightHandle(cwd=PROJ_DIR)

    # Add message handler to receive notifications and requests
    server.add_message_handler(example_message_handler)

    try:
        # Start the server
        print("Starting LSP server...")
        await server.start()
        print(f"Server started (state: {server.state})")

        # Send an initialize request (not required by base class, but typical LSP flow)
        print("\nSending initialize request...")
        result = await server.send_request(
            method="initialize",
            params={
                "processId": None,
                "rootUri": f"file://{PROJ_DIR}",
                "capabilities": {},
                "rootPath": PROJ_DIR,
                "rootUri": pathlib.Path(PROJ_DIR).as_uri(),
                "initializationOptions": {
                    "exclude": [
                        "**/__pycache__",
                        "**/.venv",
                        "**/.env",
                        "**/build",
                        "**/dist",
                        "**/.pixi",
                    ],
                    "reportMissingImports": "error",
                },
            },
            timeout=10.0,
        )

        print(f"Initialize result: {result}")

        # Send initialized notification
        print("\nSending initialized notification...")
        await server.send_notification(method="initialized", params={})

        # Send a textDocument/documentSymbol request as an example
        print("\nSending textDocument/documentSymbol request...")
        symbol_result = await server.send_request(
            method="textDocument/documentSymbol",
            params={
                "textDocument": {"uri": f"file://{THIS_FILE}"},
            },
            timeout=5.0,
        )
        print(f"Document symbols result: {symbol_result}")

        # Keep server running for a bit to receive any async notifications
        await asyncio.sleep(2)

    except Exception as e:
        print(f"Error: {e}")

    finally:
        # Stop the server
        print("\nStopping server...")
        await server.stop()
        print(f"Server stopped (state: {server.state})")


if __name__ == "__main__":
    asyncio.run(main())
