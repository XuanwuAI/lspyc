"""Example demonstrating MutilLangClient with language-to-factory mapping.

This example shows how to configure and use the MutilLangClient with
different language server factories for multiple languages.
"""

import asyncio
import os

from lspyc.handle import NativeHandleFactory
from lspyc.mlclient import DEFAULT_NATIVE_FACTORIES, MutilLangClient

THIS_FILE = os.path.abspath(__file__)
PROJ_DIR = os.path.dirname(os.path.dirname(__file__))


async def test_default_factories() -> None:
    """Test multi-language client with default factories."""
    print("\n=== Testing MutilLangClient with DEFAULT_NATIVE_FACTORIES ===")

    # Use the default factories - easiest way to get started!
    client = MutilLangClient(
        workspace_root=PROJ_DIR,
        language_factories=DEFAULT_NATIVE_FACTORIES,
    )

    try:
        # Test with a Python file
        print("\n1. Getting document symbols for Python file...")
        python_file = "examples/mlclient_example.py"
        symbols = await client.get_document_symbols(python_file)
        print(f"   Found {len(symbols)} symbols")
        if symbols:
            print(f"   First symbol: {symbols[0].get('name', 'N/A')}")

        # Test language detection
        print("\n2. Testing language detection...")
        detected_lang = client._detect_language("test.py")
        print(f"   test.py -> {detected_lang}")
        detected_lang = client._detect_language("test.rs")
        print(f"   test.rs -> {detected_lang}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.shutdown()
        print("\n3. Client shutdown complete")


async def test_custom_factories() -> None:
    """Test multi-language client with custom factories."""
    print("\n=== Testing MutilLangClient with Custom Factories ===")

    # Start with defaults and customize
    language_factories = DEFAULT_NATIVE_FACTORIES.copy()
    
    # Override Python with custom settings
    language_factories["python"] = NativeHandleFactory(
        command=["pyright-langserver", "--stdio"],
        cwd=PROJ_DIR,
    )

    # Create multi-language client
    client = MutilLangClient(
        workspace_root=PROJ_DIR,
        language_factories=language_factories,
    )

    try:
        # Test with a Python file
        print(f"\n1. Getting document symbols for {THIS_FILE} ...")
        symbols = await client.get_document_symbols(THIS_FILE)
        print(f"   Found {len(symbols)} symbols")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.shutdown()
        print("\n2. Client shutdown complete")


async def test_available_languages() -> None:
    """Test to show all available default languages."""
    print("\n=== Available Default Languages ===")
    
    print("\nDefault language factories available:")
    for lang in DEFAULT_NATIVE_FACTORIES.keys():
        print(f"   - {lang}")
    
    print("\nFile extension mappings:")
    client = MutilLangClient(
        workspace_root=PROJ_DIR,
        language_factories=DEFAULT_NATIVE_FACTORIES,
    )
    
    test_files = [
        "test.py", "test.js", "test.ts", "test.rs", 
        "test.go", "test.java", "test.cs", "test.rb",
        "test.php", "test.swift", "test.kt", "test.c", "test.cpp"
    ]
    
    for file in test_files:
        lang = client._detect_language(file)
        has_factory = lang in DEFAULT_NATIVE_FACTORIES if lang else False
        status = "✓" if has_factory else "✗"
        print(f"   {status} {file:15} -> {lang or 'not recognized':15} {'(factory available)' if has_factory else ''}")
    
    await client.shutdown()


async def test_unsupported_language() -> None:
    """Test behavior with unsupported file type."""
    print("\n=== Testing Unsupported Language ===")

    # Use only Python factory
    language_factories = {
        "python": NativeHandleFactory(
            command=["pyright-langserver", "--stdio"],
            cwd=PROJ_DIR,
        ),
    }

    client = MutilLangClient(
        workspace_root=PROJ_DIR,
        language_factories=language_factories,
    )

    try:
        # Try to use a JavaScript file (not configured)
        print("\n1. Attempting to use JavaScript file without factory...")
        await client.get_document_symbols("test.js")
    except ValueError as e:
        print(f"   Expected error: {e}")
    finally:
        await client.shutdown()


async def test_factory_validation() -> None:
    """Test factory validation during handle creation."""
    print("\n=== Testing Factory Validation ===")

    # Configure with an invalid command
    language_factories = {
        "python": NativeHandleFactory(
            command=["nonexistent-lsp-server", "--stdio"],
            cwd=PROJ_DIR,
        ),
    }

    client = MutilLangClient(
        workspace_root=PROJ_DIR,
        language_factories=language_factories,
    )

    try:
        print("\n1. Attempting to use invalid factory...")
        await client.get_document_symbols("test.py")
    except RuntimeError as e:
        print(f"   Expected error: {e}")
    finally:
        await client.shutdown()


async def main() -> None:
    """Run all examples."""
    print("=" * 60)
    print("MutilLangClient Examples")
    print("=" * 60)

    # Show available languages
    await test_available_languages()

    # Test with default factories (easiest way!)
    await test_default_factories()

    # Test with custom factories
    await test_custom_factories()

    # Test unsupported language
    await test_unsupported_language()

    # Test factory validation
    await test_factory_validation()

    print("\n" + "=" * 60)
    print("Examples completed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())