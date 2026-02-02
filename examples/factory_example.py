"""Example demonstrating factory usage for creating LSP handles.

This example shows how to use the factory classes to validate and create
different types of LSP handles (native, docker, websocket).
"""

import asyncio
import os

from lspyc.handle import (
    DockerHandleFactory,
    NativeHandleFactory,
    WebSocketHandleFactory,
)

PROJ_DIR = os.path.dirname(os.path.dirname(__file__))


async def test_native_factory() -> None:
    """Test native handle factory."""
    print("\n=== Testing Native Handle Factory ===")

    # Create factory for Pyright
    factory = NativeHandleFactory(
        command=["pyright-langserver", "--stdio"],
        cwd=PROJ_DIR,
    )

    # Validate before creating
    is_valid, error = await factory.validate()
    print(f"Validation result: {is_valid}")
    if not is_valid:
        print(f"Validation error: {error}")
        return

    # Create handle with auto-initialization
    print("Creating handle with auto-initialization...")
    handle = await factory.create(workspace_root=PROJ_DIR)
    print(f"Handle created: {type(handle).__name__}")

    # Start will automatically initialize
    try:
        capabilities = await handle.start()
        print(f"Handle started and initialized, state: {handle.state}")
        print(f"Server capabilities received: {bool(capabilities)}")

    finally:
        await handle.stop()
        print("Handle stopped")


async def test_docker_factory() -> None:
    """Test docker handle factory."""
    print("\n=== Testing Docker Handle Factory ===")

    # Create factory for Pyright in Docker
    factory = DockerHandleFactory(
        image="pyright-local",
        command=["pyright-langserver", "--stdio"],
        container_workspace="/workspace",
    )

    # Validate before creating
    is_valid, error = await factory.validate()
    print(f"Validation result: {is_valid}")
    if not is_valid:
        print(f"Validation error: {error}")
        return

    # Create handle
    print("Creating handle...")
    handle = await factory.create(PROJ_DIR)
    print(f"Handle created: {type(handle).__name__}")

    # Start and test basic communication
    try:
        await handle.start()
        print(f"Handle started, state: {handle.state}")

        # Send initialize request (using container path)
        result = await handle.send_request(
            method="initialize",
            params={
                "processId": None,
                "rootUri": "file:///workspace",
                "capabilities": {},
            },
            timeout=10.0,
        )
        print(f"Initialize successful: {bool(result)}")

        # Send initialized notification
        await handle.send_notification(method="initialized", params={})

    finally:
        await handle.stop()
        print("Handle stopped")


async def test_websocket_factory() -> None:
    """Test WebSocket handle factory."""
    print("\n=== Testing WebSocket Handle Factory ===")

    # Create factory for WebSocket server
    factory = WebSocketHandleFactory(
        url="ws://localhost:8080",
        connect_timeout=5.0,
    )

    # Validate before creating
    is_valid, error = await factory.validate()
    print(f"Validation result: {is_valid}")
    if not is_valid:
        print(f"Validation error: {error}")
        print("Note: WebSocket server needs to be running at ws://localhost:8765")
        return

    # Create handle
    print("Creating handle...")
    handle = await factory.create(PROJ_DIR)
    print(f"Handle created: {type(handle).__name__}")

    # Start and test basic communication
    try:
        await handle.start()
        print(f"Handle started, state: {handle.state}")

        # Send initialize request
        result = await handle.send_request(
            method="initialize",
            params={
                "processId": os.getpid(),
                "rootUri": f"file://{PROJ_DIR}",
                "capabilities": {},
            },
            timeout=10.0,
        )
        print(f"Initialize successful: {bool(result)}")

        # Send initialized notification
        await handle.send_notification(method="initialized", params={})

    finally:
        await handle.stop()
        print("Handle stopped")


async def test_invalid_factory() -> None:
    """Test factory validation with invalid configurations."""
    print("\n=== Testing Invalid Factory Configurations ===")

    # Test non-existent command
    print("\n1. Non-existent command:")
    factory = NativeHandleFactory(command=["nonexistent-command", "--stdio"])
    is_valid, error = await factory.validate()
    print(f"   Validation result: {is_valid}")
    print(f"   Error message: {error}")

    # Test non-existent docker image
    print("\n2. Non-existent docker image:")
    factory = DockerHandleFactory(
        image="nonexistent-image",
        command=["some-command"],
    )
    is_valid, error = await factory.validate()
    print(f"   Validation result: {is_valid}")
    print(f"   Error message: {error}")

    # Test unreachable WebSocket URL
    print("\n3. Unreachable WebSocket URL:")
    factory = WebSocketHandleFactory(
        url="ws://localhost:9999",
        connect_timeout=2.0,
    )
    is_valid, error = await factory.validate()
    print(f"   Validation result: {is_valid}")
    print(f"   Error message: {error}")


async def main() -> None:
    """Run all factory examples."""
    print("=" * 60)
    print("LSP Handle Factory Examples")
    print("=" * 60)

    # Test invalid configurations first (they should fail quickly)
    await test_invalid_factory()

    # Test valid configurations
    await test_native_factory()

    # Uncomment these if you have docker or WebSocket server set up
    await test_docker_factory()
    await test_websocket_factory()

    print("\n" + "=" * 60)
    print("Examples completed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
