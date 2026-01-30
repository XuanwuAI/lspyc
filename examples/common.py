import os
import pathlib

from lspyc.handle.base import LspHandle
from lspyc.handle.protocol import JsonRpcMessage

TARGET_FILE = __file__
PROJ_DIR = os.path.dirname(os.path.dirname(TARGET_FILE))

INIT_PARAMS = {
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
}


async def example_message_handler(message: JsonRpcMessage) -> None:
    """Example handler for incoming server messages."""
    content = message.content

    if message.is_notification:
        print(f"Received notification: {content.get('method')}")
    elif message.is_request:
        print(f"Received request: {content.get('method')}")


async def main_test(handle: LspHandle):
    handle.add_message_handler(example_message_handler)
    try:
        await handle.start()
        print(f"Handle (state: {handle.state})")

        # Send an initialize request
        print("\nSending initialize request...")
        result = await handle.send_request(
            method="initialize",
            params=INIT_PARAMS,
            timeout=10.0,
        )
        print(f"Initialize result: {result}")

        # Send initialized notification
        print("\nSending initialized notification...")
        await handle.send_notification(method="initialized", params={})

        # Send a textDocument/documentSymbol request as an example
        print("\nSending textDocument/documentSymbol request...")
        symbol_result = await handle.send_request(
            method="textDocument/documentSymbol",
            params={
                "textDocument": {"uri": f"file://{TARGET_FILE}"},
            },
            timeout=5.0,
        )
        print(f"Document symbols result: {symbol_result}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()

    finally:
        print("\nClosing handle...")
        await handle.stop()
        print(f"Handle closed (state: {handle.state})")

    pass
