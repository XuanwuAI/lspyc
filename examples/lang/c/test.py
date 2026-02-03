import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    res = await client.get_document_symbols("hello.c")
    assert res[0]["name"] == "hello"
    res = await client.get_references("hello.c", 3, 5, False)
    assert any("main.c" in t["uri"] for t in res)
    res = await client.get_definition("main.c", 3, 4)
    assert "hello.c" in res[0]["uri"]
    await client.shutdown()
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))