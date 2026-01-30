import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj", "src")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    print("Testing get_document_symbols")
    res = await client.get_document_symbols("Hello.java")
    assert res[0]["name"] == "Hello"
    print("Testing get_references")
    res = await client.get_references("Hello.java", 2, 23)
    assert any(t["uri"] == "file://" + os.path.join(proj_root, "Hello.java") for t in res)
    print("Testing get_definition")
    res = await client.get_definition("Main.java", 2, 14)
    assert res[0]["uri"] == "file://" + os.path.join(proj_root, "Hello.java")
    assert res[0]["range"]["start"]["line"] == 2
    assert res[0]["range"]["start"]["character"] == 23
    await client.shutdown()
    print("All tests passed")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))