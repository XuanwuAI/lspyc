import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    print("Testing get_document_symbols")
    res = await client.get_document_symbols("src/hello.ts")
    assert res[0]["name"] == "hello"
    print("Testing get_references")
    res = await client.get_references("src/hello.ts", 0, 16)
    assert any(
        t["uri"] == "file://" + os.path.join(proj_root, "src/index.ts") for t in res
    )
    print("Testing get_definition")
    res = await client.get_definition("src/index.ts", 2, 12)
    assert res[0]["uri"] == "file://" + os.path.join(proj_root, "src/hello.ts")
    assert res[0]["range"]["start"]["line"] == 0
    assert res[0]["range"]["start"]["character"] == 16
    await client.shutdown()
    print("All tests passed")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))
