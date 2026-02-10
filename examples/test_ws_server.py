"""Test client for the WebSocket LSP server.

This example demonstrates how to connect to the WebSocket LSP server
and interact with it using the LspWsHandle client.
"""

import asyncio
from pathlib import Path

from lspyc import MutilLangClient, WebSocketHandleFactory


async def test_pyright_server():
    """Test connecting to the pyright LSP server via WebSocket."""

    workspace_root = str(Path(__file__).parent)
    factory_map = {"python": WebSocketHandleFactory("ws://localhost:8080/", "pyright-langserver")}
    mlclient = MutilLangClient(workspace_root, factory_map)

    # Get a test workspace (use current directory or examples directory)

    print("=" * 70)
    print("Testing Pyright LSP Server via WebSocket")
    print("=" * 70)
    print(f"Workspace: {workspace_root}")

    symbol_result = await mlclient.get_document_symbols("test_ws_server.py")
    print(f"Symbol result: {symbol_result}")
    await mlclient.shutdown()


async def test_rust_analyzer_server():
    """Test connecting to the rust-analyzer LSP server via WebSocket."""
    workspace_root = str(Path(__file__).parent / "lang" / "rust" / "test-proj")
    factory_map = {
        "rust": WebSocketHandleFactory("ws://localhost:8080", "rust-analyzer")
    }
    mlclient = MutilLangClient(workspace_root, factory_map)
    print("=" * 70)
    print("Testing Rust Analyzer LSP Server via WebSocket")
    print("=" * 70)
    print(f"Workspace: {workspace_root}")

    symbol_result = await mlclient.get_document_symbols("src/main.rs")
    print(f"Symbol result: {symbol_result}")
    await mlclient.shutdown()


async def main():
    """Run the test client."""

    print("\nMake sure the WebSocket LSP server is running!")

    # Run tests
    await test_pyright_server()
    await test_rust_analyzer_server()

    print("\n" + "=" * 70)
    print("All tests completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
