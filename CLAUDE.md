# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

lspyc is a Python library for managing and querying Language Server Protocol (LSP) servers. It provides multi-language LSP client capabilities focused on **read-only code queries** (symbols, definitions, references) — it does NOT support code editing via LSP.

## Development Commands

```bash
# Install dependencies
uv sync --all-extras --dev

# No formal test suite — test via example scripts:
# Per-language examples in examples/lang/{python,rust,go,java,c,kotlin,typescript}/
# WebSocket examples: examples/test_ws_server.py, examples/test_ws_all.py

# Build Docker image with all LSP servers
docker build -t lspyc-server .

# Run WebSocket service
python -m lspyc.service
```

## Architecture

### Client Layer

- **`MutilLangClient`** (`mlclient.py`): Core async client managing multiple LSP servers (one per language). Auto-detects language from file extensions. Manages file open/close state with LRU eviction (max 20 files). All query methods return empty results on failure (no exceptions).
- **`ThreadedClient`** (`threaded.py`): Sync/async wrapper around `MutilLangClient` using a dedicated daemon thread with its own event loop. Provides both `get_*()` (sync) and `aget_*()` (async) methods, plus `batch_get_document_symbols()` for concurrent batch operations.

### Handle Layer (`handle/`)

LSP server connections are abstracted through handle types:

- **`LspHandle`** (`base.py`): Abstract base handling JSON-RPC request/response correlation and message dispatching.
- **`LspStdioHandle`**: Connects to LSP servers via stdio subprocess (used by Native and Docker factories).
- **`LspWsHandle`** (`wshandle.py`): Connects via WebSocket with auto-reconnect. Supports NFS path remapping between local and remote mount prefixes.

### Factory Layer (`handle/factory.py`)

Handle creation uses the factory pattern with four implementations:

| Factory | Description |
|---------|-------------|
| `NativeHandleFactory` | Local LSP servers via stdio (default) |
| `DockerHandleFactory` | LSP servers in Docker containers |
| `WebSocketHandleFactory` | Remote LSP servers via WebSocket |
| `AutoHandleFactory` | Tries candidates in order until one validates |

Factories are configured per-language via `language_factories` dict passed to clients, or built automatically from settings/YAML config via `factory_builder.py`.

### Supporting Components

- **`QuiescenceTracker`** (`quiescence.py`): Monitors LSP `$/progress` notifications to determine when a server has finished analyzing files. Integrated into the file-open flow to avoid premature queries.
- **`LspService`** (`service.py`): WebSocket server exposing LSP servers over network. Path-based routing (e.g., `/pyright` → pyright process). Each WebSocket connection gets a dedicated LSP process.
- **`LspycSettings`** (`settings.py`): Pydantic BaseSettings with `LSPYC_` env prefix and nested delimiter `__`. Key settings: `max_open_files`, `lsp_request_timeout`, `quiescence_timeout`, `factory_ty`.

### Supported Languages & Default LSP Servers

Python (pyright), JavaScript/TypeScript (typescript-language-server), C/C++ (clangd), Rust (rust-analyzer), Go (gopls), Java (jdtls), Kotlin (kotlin-language-server).

## Key Patterns

- **Async-first**: `MutilLangClient` is pure async. `ThreadedClient` bridges to sync via `asyncio.run_coroutine_threadsafe()`.
- **Silent failures**: Query methods catch exceptions and return empty lists rather than propagating errors.
- **Per-language locks**: `_initialization_locks` in `MutilLangClient` prevent concurrent initialization of the same language server.
- **LRU file management**: `OpenFileManager` uses `OrderedDict` to track open files and auto-evicts when limit exceeded.
- **Quiescence-aware**: After opening a file, the client waits for the LSP server to finish analysis before returning.

## Configuration

Settings can be set via environment variables:
```bash
LSPYC_MAX_OPEN_FILES=50
LSPYC_LSP_REQUEST_TIMEOUT=20.0
LSPYC_FACTORY_TY=ws
LSPYC_WS_FACTORY__BASE_URL=ws://remote:8080
```

Or via YAML config file (see `.lspyc.yaml` for format) with `factory_ty=file` and `factory_file` path.
