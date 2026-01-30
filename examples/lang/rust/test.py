import asyncio
import os

from lspyc.mlclient import MutilLangClient

FILE = os.path.abspath(__file__)
PROJ_ROOT = os.path.join(os.path.dirname(FILE), "test-proj")


async def main(proj_root: str):
    client = MutilLangClient(proj_root)
    await client.open_document("src/hello.rs")
    await asyncio.sleep(5.0)
    res = await client.get_document_symbols("src/hello.rs")
    print(res)
    res = await client.get_references("src/hello.rs", 0, 9)
    print(res)
    res = await client.get_definition("src/hello.rs", 5, 5)
    print(res)
    await client.shutdown()


if __name__ == "__main__":
    asyncio.run(main(PROJ_ROOT))
