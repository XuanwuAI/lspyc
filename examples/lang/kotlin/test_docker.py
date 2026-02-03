import asyncio
import os

from lspyc.handle import DockerHandleFactory
from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    # Create Docker factory for Kotlin LSP server
    kotlin_factory = DockerHandleFactory(
        image="lspyc-server",
        command=["kotlin-language-server"],
        container_workspace="/workspace",
    )

    # Create MutilLangClient with Docker-based Kotlin factory
    client = MutilLangClient(proj_root, language_factories={"kotlin": kotlin_factory})

    # Test 1: Get document symbols
    res = await client.get_document_symbols("src/Hello.kt")
    assert res[0]["name"] == "greet"

    # Test 2: Get references
    res = await client.get_references("src/Hello.kt", 1, 4)
    assert any("Hello.kt" in t["uri"] for t in res)

    # Test 3: Get definition
    res = await client.get_definition("src/Main.kt", 1, 4)
    assert "Hello.kt" in res[0]["uri"]

    await client.shutdown()
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))
