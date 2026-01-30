import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    print("Testing get_document_symbols")
    res = await client.get_document_symbols("test.go")
    assert res[0]["name"] == "Hello"
    print("Testing get_references")
    res = await client.get_references("test.go", 5, 5)
    assert any(r["uri"] == "file://" + os.path.join(proj_root, "test.go") for r in res)
    print("Testing get_definition")
    res = await client.get_definition("main.go", 3, 4)
    assert res[0]["uri"] == "file://" + os.path.join(proj_root, "test.go")
    assert res[0]["range"]["start"]["line"] == 5
    assert res[0]["range"]["start"]["character"] == 5
    await client.shutdown()
    print("All tests passed")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))
