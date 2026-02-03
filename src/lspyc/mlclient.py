"""LSP Manager for handling multiple language servers."""

import asyncio
import os
from collections import OrderedDict
from pathlib import Path
from typing import Mapping

from .handle import HandleFactory, LspHandle, NativeHandleFactory
from .handle.protocol import DocumentSymbol, JsonRpcMessage, Location
from .quiescence import QuiescenceTracker
from .settings import LspycSettings

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
    # "csharp": NativeHandleFactory(["omnisharp"]),
    # "ruby": NativeHandleFactory(["solargraph", "stdio"]),
    # "php": NativeHandleFactory(["intelephense", "--stdio"]),
    "swift": NativeHandleFactory(["sourcekit-lsp"]),
    # "kotlin": NativeHandleFactory(["kotlin-language-server"]),
}


class OpenFileManager:
    """Manages open files across all language servers with LRU eviction.

    Tracks which files are currently open and automatically closes the least
    recently used files when the maximum limit is reached.
    """

    def __init__(self, max_open_files: int, quiescence_timeout: float) -> None:
        """Initialize the open file manager.

        Args:
            max_open_files: Maximum number of files to keep open concurrently
            quiescence_timeout: Timeout for wait_for_quiescence operations in seconds
        """
        self._open_files: OrderedDict[str, tuple[LspHandle, str]] = OrderedDict()
        self._max_open_files = max_open_files
        self._quiescence_timeout = quiescence_timeout
        self._lock = asyncio.Lock()

    async def ensure_file_open(
        self,
        handle: LspHandle,
        abs_path: str,
        uri: str,
        language: str,
        content: str | None = None,
        tracker: QuiescenceTracker | None = None,
    ) -> None:
        """Ensure a file is open, opening it if necessary and managing LRU eviction.

        Args:
            handle: The LSP handle for the file's language
            abs_path: Absolute path to the file
            uri: File URI
            language: Language ID
            content: Optional file content. If None, reads from file system
            tracker: Optional quiescence tracker to wait for server readiness after opening
        """
        async with self._lock:
            # If file is already open, just mark it as used
            if uri in self._open_files:
                self.mark_file_used(uri)
                return

            # If at capacity, evict the least recently used file
            if len(self._open_files) >= self._max_open_files:
                await self._evict_lru()

            # Read file content if not provided
            if content is None:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()

            # Open the file
            await handle.send_notification(
                method="textDocument/didOpen",
                params={
                    "textDocument": {
                        "uri": uri,
                        "languageId": language,
                        "version": 1,
                        "text": content,
                    }
                },
            )

            # Track the opened file
            self._open_files[uri] = (handle, language)

        # Wait for quiescence outside the lock (if file was just opened)
        if tracker:
            await tracker.wait_for_quiescence(timeout=self._quiescence_timeout)

    def mark_file_used(self, uri: str) -> None:
        """Mark a file as recently used, moving it to the end of the LRU queue.

        Args:
            uri: File URI
        """
        if uri in self._open_files:
            # Move to end (most recently used)
            self._open_files.move_to_end(uri)

    async def close_file(self, uri: str) -> None:
        """Close a specific file.

        Args:
            uri: File URI to close
        """
        if uri in self._open_files:
            handle, _ = self._open_files[uri]

            # Send didClose notification
            await handle.send_notification(
                method="textDocument/didClose",
                params={"textDocument": {"uri": uri}},
            )

            # Remove from tracking
            del self._open_files[uri]

    async def close_all(self) -> None:
        """Close all open files."""
        # Create a list of URIs to avoid modifying dict during iteration
        uris = list(self._open_files.keys())
        for uri in uris:
            try:
                await self.close_file(uri)
            except Exception:
                # Continue closing other files even if one fails
                pass

    async def _evict_lru(self) -> None:
        """Evict the least recently used file."""
        if self._open_files:
            # Get the first item (least recently used)
            lru_uri = next(iter(self._open_files))
            await self.close_file(lru_uri)


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
        # ".cs": "csharp",
        # Ruby
        # ".rb": "ruby",
        # PHP
        # ".php": "php",
        # Swift
        # ".swift": "swift",
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
        settings: LspycSettings | None = None,
    ) -> None:
        """Initialize the LSP manager for a workspace.

        Args:
            workspace_root: Root directory path for the workspace
            language_factories: Mapping of language IDs to HandleFactory instances
                               Example: {"python": NativeHandleFactory(["pyright-langserver", "--stdio"])}
            settings: Optional settings instance. If not provided, defaults will be used.

        Raises:
            ValueError: If language_factories is empty
        """

        self.workspace_root = os.path.abspath(workspace_root)
        self._language_factories = dict(language_factories)
        self._handles: dict[str, LspHandle] = {}
        self._initialization_locks: dict[str, asyncio.Lock] = {}
        self._quiescence_trackers: dict[str, QuiescenceTracker] = {}
        self._settings = settings or LspycSettings()
        self._file_manager = OpenFileManager(
            max_open_files=self._settings.max_open_files,
            quiescence_timeout=self._settings.quiescence_timeout
        )

    async def validate_handle_factories(self) -> None:
        """Validate all configured handle factories."""
        for language, factory in self._language_factories.items():
            try:
                await factory.validate()
            except Exception as e:
                raise RuntimeError(f"Failed to validate {language} factory: {e}") from e

    async def get_document_symbols(self, file_path: str) -> list[DocumentSymbol]:
        """Get document symbols for a file.

        Args:
            file_path: Path to the file (absolute or relative to workspace)

        Returns:
            List of document symbols

        Raises:
            ValueError: If the file type is not supported
            RuntimeError: If the operation fails
        """
        try:
            handle, uri = await self._prepare_query(file_path)
            result: list[DocumentSymbol] = (
                await handle.send_request(
                    method="textDocument/documentSymbol",
                    params={"textDocument": {"uri": uri}},
                    timeout=self._settings.lsp_request_timeout,
                )
                or []
            )
            return result
        except Exception:
            # TODO: log
            return []

    async def get_definition(
        self, file_path: str, line: int, character: int
    ) -> list[Location]:
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
        try:
            handle, uri = await self._prepare_query(file_path)
            result: list[Location] = []
            _result = await handle.send_request(
                method="textDocument/definition",
                params={
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                },
                timeout=self._settings.lsp_request_timeout,
            )
            # Normalize result to always be a list
            if _result is None:
                return []
            elif isinstance(_result, list):
                result = _result
            else:
                result = [_result]
            return [self._localize_loc(loc, handle) for loc in result]
        except Exception:
            # TODO: log
            return []

    async def get_references(
        self,
        file_path: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[Location]:
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
        try:
            handle, uri = await self._prepare_query(file_path)
            result: list[Location] = (
                await handle.send_request(
                    method="textDocument/references",
                    params={
                        "textDocument": {"uri": uri},
                        "position": {"line": line, "character": character},
                        "context": {"includeDeclaration": include_declaration},
                    },
                    timeout=self._settings.lsp_request_timeout,
                )
                or []
            )
            return [self._localize_loc(loc, handle) for loc in result]
        except Exception:
            # TODO: log
            return []

    async def shutdown(self, timeout: float | None = None) -> None:
        """Shutdown all LSP servers.

        Args:
            timeout: Maximum time to wait for each server to shutdown. 
                    If None, uses the configured lsp_request_timeout.
        """
        if timeout is None:
            timeout = self._settings.lsp_request_timeout
        
        for _, handle in self._handles.items():
            try:
                if handle.is_running:
                    await handle.stop(timeout=timeout)
            except Exception:
                # Continue shutting down other servers even if one fails
                pass

        self._handles.clear()
        self._initialization_locks.clear()
        self._quiescence_trackers.clear()

    async def _on_request(self, handle: LspHandle, message: JsonRpcMessage) -> None:
        """Handle incoming requests from the LSP server.

        Args:
            handle: The handle that received the request
            message: The request message
        """
        d = message.to_dict()
        request_id = d["id"]
        method = d.get("method")

        if method == "window/workDoneProgress/create":
            # Extract token and mark work as started
            params = d.get("params", {})
            token = params.get("token")
            if token:
                tracker = self._get_tracker_for_handle(handle)
                if tracker:
                    await tracker.mark_work_started(str(token))

            # Send success response
            await handle.send_response(request_id, result=None)

    async def _on_notification(
        self, handle: LspHandle, message: JsonRpcMessage
    ) -> None:
        """Handle incoming notifications from the LSP server.

        Args:
            handle: The handle that received the notification
            message: The notification message
        """
        d = message.to_dict()
        method = d.get("method")

        if method == "$/progress":
            # Track progress notifications for quiescence
            params = d.get("params", {})
            token = params.get("token")
            value = params.get("value", {})
            kind = value.get("kind")

            if token:
                tracker = self._get_tracker_for_handle(handle)
                if tracker:
                    if kind == "begin":
                        await tracker.mark_work_started(str(token))
                    elif kind == "end":
                        await tracker.mark_work_ended(str(token))

    def _get_tracker_for_handle(self, handle: LspHandle) -> QuiescenceTracker | None:
        """Get the quiescence tracker for a given handle.

        Args:
            handle: The LSP handle

        Returns:
            The QuiescenceTracker for the handle, or None if not found
        """
        for language, h in self._handles.items():
            if h is handle:
                return self._quiescence_trackers.get(language)
        return None

    def _localize_loc(self, loc: Location, handle: LspHandle) -> Location:
        loc = loc.copy()
        rel_path = handle.uri2rel(loc["uri"])
        loc["uri"] = Path(self._resolve_file_path(rel_path)).as_uri()
        return loc

    async def _prepare_query(self, file_path: str) -> tuple[LspHandle, str]:
        abs_path = self._resolve_file_path(file_path)
        language = self._detect_language(abs_path)
        if language is None:
            raise ValueError(f"Unsupported file type: {file_path}")
        handle = await self._ensure_handle(language)
        uri = handle.build_uri(self._get_relative_path(abs_path))

        tracker = self._quiescence_trackers.get(language)
        await self._file_manager.ensure_file_open(
            handle, abs_path, uri, language, tracker=tracker
        )
        return handle, uri

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
            # Currently, we only support files within the workspace
            assert (
                os.path.commonpath([self.workspace_root, file_path])
                == self.workspace_root
            )
            return file_path
        return os.path.abspath(os.path.join(self.workspace_root, file_path))

    def _get_relative_path(self, abs_path: str) -> str:
        """Get relative path from absolute path.

        Args:
            abs_path: Absolute file path

        Returns:
            Path relative to workspace root
        """
        try:
            return os.path.relpath(abs_path, self.workspace_root)
        except ValueError:
            # If abs_path is on a different drive on Windows, relpath fails
            # In this case, just return the absolute path
            return abs_path

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
            # Create quiescence tracker for this language
            self._quiescence_trackers[language] = QuiescenceTracker(
                grace_period=self._settings.grace_period
            )
            return handle

    async def _get_handle_for_file(self, file_path: str) -> LspHandle:
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
        return await self._ensure_handle(language)
