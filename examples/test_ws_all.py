"""Test all language examples using WebSocket handles against a Docker-hosted LSP service.

Prerequisites:
    1. Build the Docker image:  docker build -t lspyc-server .
    2. Start the service:
        docker run -d --name lspyc-ws-test \
            -p 8080:8080 \
            -e LSPYC_SERVICE_HOST=0.0.0.0 \
            -v <host_examples_dir>:/data/nfs \
            lspyc-server \
            python -m lspyc.service

Usage:
    python examples/test_ws_all.py                    # run all tests
    python examples/test_ws_all.py python typescript   # run specific languages
"""

import asyncio
import os
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from lspyc import MutilLangClient, WebSocketHandleFactory

TestFunc = Callable[[MutilLangClient, str], Coroutine[Any, Any, None]]

EXAMPLES_DIR = Path(__file__).parent / "lang"
WS_BASE_URL = os.getenv("LSPYC_TEST_WS_URL", "ws://localhost:8080/")

# Local mount prefix: the host-side path to the examples/lang directory
LOCAL_MOUNT_PREFIX = str(EXAMPLES_DIR)
# Remote mount prefix: where Docker mounts it inside the container
REMOTE_MOUNT_PREFIX = os.getenv("LSPYC_TEST_REMOTE_PREFIX", "/data/nfs")

# Language -> (lsp_server_name, proj_root_relative_to_lang_dir, test_func)
# Each test_func takes (client, proj_root_str) and asserts correctness.


async def test_python(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("hello.py")
    assert len(res) > 0, "No symbols found"
    assert res[0]["name"] == "hello"
    res = await client.get_references("hello.py", 0, 4)
    assert any("main.py" in t["uri"] for t in res)
    res = await client.get_definition("main.py", 0, 18)
    assert "hello.py" in res[0]["uri"]


async def test_typescript(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("src/hello.ts")
    assert res[0]["name"] == "hello"
    res = await client.get_references("src/hello.ts", 0, 16)
    assert any("src/index.ts" in t["uri"] for t in res)
    res = await client.get_definition("src/index.ts", 2, 12)
    assert "src/hello.ts" in res[0]["uri"]


async def test_go(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("test.go")
    assert res[0]["name"] == "Hello"
    res = await client.get_references("test.go", 5, 5)
    assert any("test.go" in r["uri"] for r in res)
    res = await client.get_definition("main.go", 3, 4)
    assert "test.go" in res[0]["uri"]


async def test_rust(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("src/hello.rs")
    assert res[0]["name"] == "hello"
    res = await client.get_references("src/hello.rs", 0, 9)
    assert any("src/main.rs" in t["uri"] for t in res)
    res = await client.get_definition("src/main.rs", 3, 12)
    assert "src/hello.rs" in res[0]["uri"]


async def test_c(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("hello.c")
    assert res[0]["name"] == "hello"
    res = await client.get_references("hello.c", 3, 5, False)
    assert any("main.c" in t["uri"] for t in res)
    res = await client.get_definition("main.c", 3, 4)
    assert "hello.c" in res[0]["uri"]


async def test_java(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("Hello.java")
    assert res[0]["name"] == "Hello"
    res = await client.get_references("Hello.java", 2, 23)
    assert any("Hello.java" in t["uri"] for t in res)
    res = await client.get_definition("Main.java", 2, 14)
    assert "Hello.java" in res[0]["uri"]


async def test_kotlin(client: MutilLangClient, proj_root: str) -> None:
    res = await client.get_document_symbols("src/Hello.kt")
    assert res[0]["name"] == "greet"
    res = await client.get_references("src/Hello.kt", 1, 4)
    assert any("Hello.kt" in t["uri"] for t in res)
    res = await client.get_definition("src/Main.kt", 1, 4)
    assert "Hello.kt" in res[0]["uri"]


LANGUAGE_TESTS: dict[str, tuple[str, str, TestFunc]] = {
    # lang: (lsp_server_name, proj_subdir, test_func)
    "python": ("pyright-langserver", "python/test-proj", test_python),
    "typescript": ("typescript-language-server", "typescript/test-proj", test_typescript),
    "go": ("gopls", "go/test-proj", test_go),
    "rust": ("rust-analyzer", "rust/test-proj", test_rust),
    # "c": ("clangd", "c/test-proj", test_c),
    "java": ("jdtls", "java/test-proj/src", test_java),
    "kotlin": ("kotlin-language-server", "kotlin/test-proj", test_kotlin),
}


async def run_test(lang: str) -> bool:
    server_name, proj_subdir, test_func = LANGUAGE_TESTS[lang]
    proj_root = str(EXAMPLES_DIR / proj_subdir)

    factory = WebSocketHandleFactory(
        WS_BASE_URL,
        server_name,
        local_mount_prefix=LOCAL_MOUNT_PREFIX,
        remote_mount_prefix=REMOTE_MOUNT_PREFIX,
    )

    # Verify remapping
    remapped = factory._remap_workspace(proj_root)
    print(f"  workspace: {proj_root}")
    print(f"  remapped:  {remapped}")

    client = MutilLangClient(proj_root, language_factories={lang: factory})
    try:
        await test_func(client, proj_root)
        return True
    finally:
        await client.shutdown()


async def main() -> None:
    requested = sys.argv[1:] if len(sys.argv) > 1 else list(LANGUAGE_TESTS.keys())
    invalid = [l for l in requested if l not in LANGUAGE_TESTS]
    if invalid:
        print(f"Unknown languages: {invalid}")
        print(f"Available: {list(LANGUAGE_TESTS.keys())}")
        sys.exit(1)

    passed, failed = [], []
    for lang in requested:
        print(f"\n{'='*60}")
        print(f"Testing {lang}")
        print(f"{'='*60}")
        try:
            ok = await run_test(lang)
            if ok:
                print(f"  PASSED")
                passed.append(lang)
        except Exception as e:
            print(f"  FAILED: {e}")
            failed.append(lang)

    print(f"\n{'='*60}")
    print(f"Results: {len(passed)} passed, {len(failed)} failed")
    if passed:
        print(f"  Passed: {', '.join(passed)}")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
        sys.exit(1)
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
