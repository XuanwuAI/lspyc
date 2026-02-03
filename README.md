# lspyc

A Python library for managing and using Language Server Protocol (LSP) servers.

At present, it focuses on a small set of code-query features (e.g., symbol and reference lookups).
Code modification/editing is not supported.

## Features

- Multi-language support with automatic language detection
- Start and manage servers via CLI commands, Docker, or remote connections (WebSocket)
- Asynchronous API with robust error handling


## Supported Languages

lspyc supports the following programming languages:

- **Python** (.py) - via Pyright language server
- **JavaScript** (.js) - via TypeScript language server
- **TypeScript** (.ts) - via TypeScript language server
- **C/C++** (.c, .h, .cpp, .cc, .cxx, .hpp, .hh, .hxx) - via Clangd
- **Rust** (.rs) - via Rust Analyzer
- **Go** (.go) - via Gopls
- **Java** (.java) - via JDTLS
- **Kotlin** (.kt, .kts) - via Kotlin language server

## Supported Handle Types

lspyc supports three types of LSP server handles:

1. **Native Handle** - For local LSP servers communicating via standard input/output
2. **Docker Handle** - For LSP servers running inside Docker containers
3. **WebSocket Handle** - For remote LSP servers accessed via WebSocket connections

## Quick Start

```python
import asyncio
from lspyc.mlclient import MutilLangClient

async def main():
    client = MutilLangClient("/path/to/workspace")
    
    # Get document symbols
    symbols = await client.get_document_symbols("src/main.py")
    
    # Get definition locations
    definitions = await client.get_definition("src/main.py", 0, 18)
    
    # Get references
    references = await client.get_references("src/main.py", 0, 18)
    
    await client.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```
