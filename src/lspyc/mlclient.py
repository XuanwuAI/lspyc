"""LSP Manager for handling multiple language servers."""

import asyncio
import os
from pathlib import Path
from typing import Any, Mapping

from .handle import HandleFactory, LspHandle, NativeHandleFactory
from .handle.protocol import JsonRpcMessage

# Default native LSP server factories for supported languages
# Users can use this as a starting point or create their own custom mappings
DEFAULT_NATIVE_FACTORIES: dict[str, HandleFactory] = {
    "python": NativeHandleFactory(["pyright-langserver", "--stdio"]),
    "javascript": NativeHandleFactory(["typescript-language-server", "--stdio"]),
    "typescript": NativeHandleFactory(["typescript-language-server", "--stdio"]),
    "c": NativeHandleFactory(["clangd"]),
    "cpp": NativeHandleFactory(["clangd"]),
    "rust": NativeHandleFactory(["rust-analyzer"]),
    "go": NativeHandleFactory(["gopls"]),
    "java": NativeHandleFactory(["jdtls"]),
    "csharp": NativeHandleFactory(["omnisharp"]),
    "ruby": NativeHandleFactory(["solargraph", "stdio"]),
    "php": NativeHandleFactory(["intelephense", "--stdio"]),
    "swift": NativeHandleFactory(["sourcekit-lsp"]),
    "kotlin": NativeHandleFactory(["kotlin-language-server"]),
}


class MutilLangClient:
    """A multi-language LSP client that manages multiple language servers.

    The client handles automatic server lifecycle, initialization, and routing
    based on file types. It maintains one LSP server handle per language and
    automatically initializes them on first use.

    Example:
        >>> client = MutilLangClient("/path/to/workspace")
        >>> symbols = await client.get_document_symbols("src/main.py")
        >>> await client.shutdown()
    """

    # Default file extension to language ID mapping
    _EXTENSION_TO_LANGUAGE = {
        # Python
        ".py": "python",
        # JavaScript/TypeScript
        ".js": "javascript",
        ".ts": "typescript",
        # C/C++
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".hh": "cpp",
        ".hxx": "cpp",
        # Rust
        ".rs": "rust",
        # Go
        ".go": "go",
        # Java
        ".java": "java",
        # C#
        ".cs": "csharp",
        # Ruby
        ".rb": "ruby",
        # PHP
        ".php": "php",
        # Swift
        ".swift": "swift",
        # Kotlin
        ".kt": "kotlin",
        ".kts": "kotlin",
        # TODO: web? shell? vue? jsx, tsx?
        # .h might not be enough to determine the language
    }

    def __init__(
        self,
        workspace_root: str,
        language_factories: Mapping[str, HandleFactory] = DEFAULT_NATIVE_FACTORIES,
    ) -> None:
        """Initialize the LSP manager for a workspace.

        Args:
            workspace_root: Root directory path for the workspace
            language_factories: Mapping of language IDs to HandleFactory instances
                               Example: {"python": NativeHandleFactory(["pyright-langserver", "--stdio"])}

        Raises:
            ValueError: If language_factories is empty
        """

        self.workspace_root = os.path.abspath(workspace_root)
        self._language_factories = dict(language_factories)
        self._handles: dict[str, LspHandle] = {}
        self._initialization_locks: dict[str, asyncio.Lock] = {}

    def _detect_language(self, file_path: str) -> str | None:
        """Detect the language ID from a file path.

        Args:
            file_path: Path to the file

        Returns:
            Language ID (e.g., 'python') or None if not recognized
        """
        # Get the file extension
        path = Path(file_path)

        # Check file extension
        extension = path.suffix.lower()
        return self._EXTENSION_TO_LANGUAGE.get(extension)

    def _resolve_file_path(self, file_path: str) -> str:
        """Resolve a file path to an absolute path.

        Args:
            file_path: Absolute or relative file path

        Returns:
            Absolute file path
        """
        if os.path.isabs(file_path):
            return file_path
        return os.path.abspath(os.path.join(self.workspace_root, file_path))

    def _path_to_uri(self, file_path: str) -> str:
        """Convert a file path to a URI.

        Args:
            file_path: Absolute file path

        Returns:
            File URI (e.g., 'file:///path/to/file')
        """
        return Path(file_path).as_uri()

    async def _ensure_handle(self, language: str) -> LspHandle:
        """Ensure a handle exists for the given language, creating it if necessary.

        Args:
            language: Language ID (e.g., 'python')

        Returns:
            The LSP handle for the language

        Raises:
            ValueError: If no factory is configured for the language
            RuntimeError: If factory validation fails or handle creation fails
        """
        if language in self._handles:
            return self._handles[language]

        if language not in self._initialization_locks:
            self._initialization_locks[language] = asyncio.Lock()

        # Ensure only one coroutine initializes the handle at a time
        async with self._initialization_locks[language]:
            # Get factory for this language
            factory = self._language_factories.get(language)
            if factory is None:
                raise ValueError(
                    f"No factory configured for language '{language}'. "
                    f"Available languages: {list(self._language_factories.keys())}"
                )

            # TODO: maybe we have to identify the correct workspace root for
            # different languages
            handle = await factory.create(self.workspace_root)
            # Set handlers before starting
            if handle.request_handler is None:
                handle.request_handler = self._on_request
            if handle.notification_handler is None:
                handle.notification_handler = self._on_notification
            await handle.start()
            self._handles[language] = handle
            return handle

    async def _get_handle_for_file(self, file_path: str) -> tuple[LspHandle, str]:
        """Get the appropriate handle for a file.

        Args:
            file_path: Path to the file

        Returns:
            Tuple of (handle, absolute_file_path)

        Raises:
            ValueError: If the file type is not supported
            RuntimeError: If the server fails to start or initialize
        """
        language = self._detect_language(file_path)
        if language is None:
            raise ValueError(f"Unsupported file type: {file_path}")

        abs_path = self._resolve_file_path(file_path)
        handle = await self._ensure_handle(language)

        return handle, abs_path

    async def validate_handle_factories(self) -> None:
        """Validate all configured handle factories."""
        for language, factory in self._language_factories.items():
            try:
                await factory.validate()
            except Exception as e:
                raise RuntimeError(f"Failed to validate {language} factory: {e}") from e

    async def open_document(self, file_path: str, content: str | None = None) -> None:
        """Open a document in the LSP server.

        Some language servers (like TypeScript) require files to be explicitly opened
        before they can provide symbols and other information.

        Args:
            file_path: Path to the file (absolute or relative to workspace)
            content: Optional file content. If None, reads from file system

        Raises:
            ValueError: If the file type is not supported
            RuntimeError: If the operation fails
            FileNotFoundError: If file doesn't exist and content is not provided
        """
        handle, abs_path = await self._get_handle_for_file(file_path)

        # Read file content if not provided
        if content is None:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()

        language = self._detect_language(file_path)

        await handle.send_notification(
            method="textDocument/didOpen",
            params={
                "textDocument": {
                    "uri": self._path_to_uri(abs_path),
                    "languageId": language,
                    "version": 1,
                    "text": content,
                }
            },
        )

    async def get_document_symbols(self, file_path: str) -> list[dict[str, Any]]:
        """Get document symbols for a file.

        Args:
            file_path: Path to the file (absolute or relative to workspace)

        Returns:
            List of document symbols

        Raises:
            ValueError: If the file type is not supported
            RuntimeError: If the operation fails
        """
        handle, abs_path = await self._get_handle_for_file(file_path)

        result = await handle.send_request(
            method="textDocument/documentSymbol",
            params={"textDocument": {"uri": self._path_to_uri(abs_path)}},
            timeout=10.0,
        )

        return result if result else []

    async def get_definition(
        self, file_path: str, line: int, character: int
    ) -> list[dict[str, Any]]:
        """Get definition locations for a symbol.

        Args:
            file_path: Path to the file (absolute or relative to workspace)
            line: Zero-based line number
            character: Zero-based character offset

        Returns:
            List of definition locations

        Raises:
            ValueError: If the file type is not supported
            RuntimeError: If the operation fails
        """
        handle, abs_path = await self._get_handle_for_file(file_path)

        result = await handle.send_request(
            method="textDocument/definition",
            params={
                "textDocument": {"uri": self._path_to_uri(abs_path)},
                "position": {"line": line, "character": character},
            },
            timeout=10.0,
        )

        # Normalize result to always be a list
        if result is None:
            return []
        elif isinstance(result, list):
            return result
        else:
            return [result]

    async def get_references(
        self,
        file_path: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[dict[str, Any]]:
        """Get references to a symbol.

        Args:
            file_path: Path to the file (absolute or relative to workspace)
            line: Zero-based line number
            character: Zero-based character offset
            include_declaration: Whether to include the declaration

        Returns:
            List of reference locations

        Raises:
            ValueError: If the file type is not supported
            RuntimeError: If the operation fails
        """
        handle, abs_path = await self._get_handle_for_file(file_path)

        result = await handle.send_request(
            method="textDocument/references",
            params={
                "textDocument": {"uri": self._path_to_uri(abs_path)},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
            timeout=10.0,
        )

        return result if result else []

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown all LSP servers.

        Args:
            timeout: Maximum time to wait for each server to shutdown
        """
        for _, handle in self._handles.items():
            try:
                if handle.is_running:
                    await handle.stop(timeout=timeout)
            except Exception:
                # Continue shutting down other servers even if one fails
                pass

        self._handles.clear()
        self._initialization_locks.clear()

    async def _on_request(self, handle: LspHandle, message: JsonRpcMessage) -> None:
        d = message.to_dict()
        request_id = d["id"]
        if d["method"] == "window/workDoneProgress/create":
            res = await handle.send_response(request_id)

    async def _on_notification(
        self, handle: LspHandle, message: JsonRpcMessage
    ) -> None:
        # TODO: implement notification handling
        pass
