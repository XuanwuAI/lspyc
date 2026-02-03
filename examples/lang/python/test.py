import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    print("Testing get_document_symbols")
    res = await client.get_document_symbols("hello.py")
    assert len(res) > 0, "No symbols found"
    assert res[0]["name"] == "hello"
    print("Testing get_references")
    res = await client.get_references("hello.py", 0, 4)
    assert any("main.py" in t["uri"] for t in res)
    print("Testing get_definition")
    res = await client.get_definition("main.py", 0, 18)
    assert "hello.py" in res[0]["uri"]
    await client.shutdown()
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))