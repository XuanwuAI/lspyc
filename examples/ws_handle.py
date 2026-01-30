"""Example of using LspWsHandle to connect to a remote LSP server via WebSocket.

This example shows how to use LspWsHandle to connect to an LSP server
that is accessible via WebSocket (ws:// or wss://).
"""

import asyncio

from lspyc.handle import LspWsHandle

from common import main_test


async def main() -> None:
    ws_url = "ws://localhost:8080/lsp"
    handle = LspWsHandle(
        url=ws_url,
        connect_timeout=10.0,
        reconnect_delay=2.0,
        max_reconnect_attempts=-1,  # -1 for infinite reconnection attempts
    )

    print("=== Basic WebSocket LSP Example ===\n")
    await main_test(handle)

    print("\n\n=== Reconnection Example ===")
    await reconnection_example(handle)


async def reconnection_example(handle: LspWsHandle) -> None:
    try:
        print("Connecting to WebSocket server...")
        await handle.start()
        print("Connected successfully")

        # Send initialize request
        result = await handle.send_request(
            method="initialize",
            params={"processId": None, "rootUri": None, "capabilities": {}},
            timeout=10.0,
        )
        print(f"Initialized: {result}")

        # If the server disconnects, the handle will automatically try to reconnect
        # up to 5 times with a 2-second delay between attempts
        print("\nConnection will auto-reconnect if server disconnects...")
        print("Press Ctrl+C to stop")

        # Keep running indefinitely
        while handle.state != "stopped":
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await handle.stop()
        print("Connection closed")


if __name__ == "__main__":
    asyncio.run(main())
