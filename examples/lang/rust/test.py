import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    res = await client.get_document_symbols("src/hello.rs")
    await asyncio.sleep(5.0) # Wait for server
    assert res[0]["name"] == "hello"
    res = await client.get_references("src/hello.rs", 0, 9)
    assert any("src/main.rs" in t["uri"] for t in res)
    res = await client.get_definition("src/main.rs", 3, 12)
    assert "src/hello.rs" in res[0]["uri"]
    await client.shutdown()
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))
