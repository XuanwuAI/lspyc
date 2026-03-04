"""LSP Manager for handling multiple language servers."""

import asyncio
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

from .factory_builder import build_factories
from .handle import HandleFactory, HandleUnavailableError, LspHandle
from .handle.protocol import DocumentSymbol, JsonRpcMessage, Location
from .quiescence import QuiescenceTracker
from .settings import LspycSettings

logger = logging.getLogger(__name__)


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
        self._in_use: dict[str, int] = {}  # uri → active ref count
        self._max_open_files = max_open_files
        self._quiescence_timeout = quiescence_timeout
        self._cond = asyncio.Condition()

    @asynccontextmanager
    async def use_file(
        self,
        handle: LspHandle,
        abs_path: str,
        uri: str,
        language: str,
        content: str | None = None,
        tracker: QuiescenceTracker | None = None,
    ) -> AsyncIterator[None]:
        """Open a file with ref-counted eviction protection.

        While the context manager is held, the file cannot be evicted by LRU.
        """
        needs_quiescence = False
        async with self._cond:
            while True:
                # Case 1: Already open — bump ref count + LRU position
                if uri in self._open_files:
                    self._open_files.move_to_end(uri)
                    self._in_use[uri] = self._in_use.get(uri, 0) + 1
                    break

                # Case 2: Below limit — open directly
                if len(self._open_files) < self._max_open_files:
                    await self._do_open(handle, abs_path, uri, language, content)
                    self._in_use[uri] = 1
                    needs_quiescence = True
                    break

                # Case 3: At limit — try evicting a non-in-use file
                evicted = self._find_evictable()
                if evicted is not None:
                    await self._do_close(evicted)
                    await self._do_open(handle, abs_path, uri, language, content)
                    self._in_use[uri] = 1
                    needs_quiescence = True
                    break

                # Case 4: All files in-use — wait for a release
                await self._cond.wait()

        # Quiescence wait outside the lock (can be slow)
        if needs_quiescence and tracker:
            await tracker.wait_for_quiescence(timeout=self._quiescence_timeout)

        try:
            yield
        finally:
            async with self._cond:
                if uri in self._in_use:
                    self._in_use[uri] -= 1
                    if self._in_use[uri] <= 0:
                        del self._in_use[uri]
                self._cond.notify_all()

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

        Note: This method does NOT provide eviction protection. Prefer use_file()
        for queries that need the file to remain open.
        """
        async with self._cond:
            if uri in self._open_files:
                self._open_files.move_to_end(uri)
                return

            if len(self._open_files) >= self._max_open_files:
                await self._evict_lru()

            await self._do_open(handle, abs_path, uri, language, content)

        if tracker:
            await tracker.wait_for_quiescence(timeout=self._quiescence_timeout)

    async def close_file(self, uri: str) -> None:
        """Close a specific file.

        Args:
            uri: File URI to close
        """
        async with self._cond:
            await self._do_close(uri)

    async def close_all(self) -> None:
        """Close all open files."""
        async with self._cond:
            uris = list(self._open_files.keys())
            for uri in uris:
                try:
                    await self._do_close(uri)
                except Exception:
                    pass
            self._in_use.clear()

    async def _do_open(
        self,
        handle: LspHandle,
        abs_path: str,
        uri: str,
        language: str,
        content: str | None = None,
    ) -> None:
        """Send didOpen notification. Must be called with self._cond held."""
        if content is None:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
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
        self._open_files[uri] = (handle, language)

    async def _do_close(self, uri: str) -> None:
        """Send didClose notification and remove tracking. Must be called with self._cond held."""
        if uri in self._open_files:
            handle, _ = self._open_files[uri]
            await handle.send_notification(
                method="textDocument/didClose",
                params={"textDocument": {"uri": uri}},
            )
            del self._open_files[uri]

    def _find_evictable(self) -> str | None:
        """Find the LRU file that is NOT in-use. Returns uri or None."""
        for uri in self._open_files:
            if uri not in self._in_use:
                return uri
        return None

    async def _evict_lru(self) -> None:
        """Evict the least recently used file that is not in-use."""
        evicted = self._find_evictable()
        if evicted is not None:
            await self._do_close(evicted)

    async def invalidate_language(self, language: str) -> None:
        """Clear tracking for a language without sending didClose (old handle is dead)."""
        async with self._cond:
            to_remove = [
                uri for uri, (_, lang) in self._open_files.items() if lang == language
            ]
            for uri in to_remove:
                del self._open_files[uri]
                # Only clear _in_use if not actively held by a use_file context
                if self._in_use.get(uri, 0) <= 0:
                    self._in_use.pop(uri, None)
            if to_remove:
                self._cond.notify_all()


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
        language_factories: Mapping[str, HandleFactory] | None = None,
        settings: LspycSettings = LspycSettings(),
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
        self._handles: dict[str, LspHandle] = {}
        self._initialization_locks: dict[str, asyncio.Lock] = {}
        self._quiescence_trackers: dict[str, QuiescenceTracker] = {}
        self._language_factories = language_factories or build_factories(
            settings.factory_ty,
            settings.docker_factory,
            settings.ws_factory,
            settings.factory_file,
        )
        self._file_manager = OpenFileManager(
            max_open_files=settings.max_open_files,
            quiescence_timeout=settings.quiescence_timeout,
        )
        self._settings = settings
        self._restart_locks: dict[str, asyncio.Lock] = {}
        self._max_retries = settings.max_retries

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
        """
        try:
            result = await self._request_with_retry(
                file_path=file_path,
                method="textDocument/documentSymbol",
                params_builder=lambda h, uri: {"textDocument": {"uri": uri}},
            )
            return result or []
        except Exception as e:
            logger.error(
                f"get_document_symbols failed for {file_path}: {e} ({type(e)})"
            )
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
        """
        try:
            result = await self._request_with_retry(
                file_path=file_path,
                method="textDocument/definition",
                params_builder=lambda h, uri: {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                },
            )
            # Normalize result to always be a list
            if result is None:
                return []
            elif isinstance(result, list):
                locs = result
            else:
                locs = [result]
            # Localize using the current handle
            abs_path = self._resolve_file_path(file_path)
            language = self._detect_language(abs_path)
            handle = self._handles.get(language) if language else None  # type: ignore[arg-type]
            if handle:
                return [self._localize_loc(loc, handle) for loc in locs]
            return locs
        except Exception as e:
            logger.error(
                f"get_definition failed for {file_path} "
                f"(line: {line}, character: {character}): {e} ({type(e)})"
            )
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
        """
        try:
            result = await self._request_with_retry(
                file_path=file_path,
                method="textDocument/references",
                params_builder=lambda h, uri: {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                    "context": {"includeDeclaration": include_declaration},
                },
            )
            locs: list[Location] = result or []
            # Localize using the current handle
            abs_path = self._resolve_file_path(file_path)
            language = self._detect_language(abs_path)
            handle = self._handles.get(language) if language else None  # type: ignore[arg-type]
            if handle:
                return [self._localize_loc(loc, handle) for loc in locs]
            return locs
        except Exception as e:
            logger.error(
                f"get_references failed for {file_path} "
                f"(line: {line}, character: {character}): {e} ({type(e)})"
            )
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
                await handle.stop(timeout=timeout)
            except Exception:
                # Continue shutting down other servers even if one fails
                pass

        self._handles.clear()
        self._initialization_locks.clear()
        for tracker in self._quiescence_trackers.values():
            await tracker.close()
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

        elif method == "language/status":
            # jdtls sends language/status during initialization.
            # Treat each message as activity to reset the grace period timer.
            tracker = self._get_tracker_for_handle(handle)
            if tracker:
                token = "__language_status__"
                await tracker.mark_work_started(token)
                await tracker.mark_work_ended(token)

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

    async def _restart_handle(self, language: str) -> None:
        """Restart the handle for a language after a connection failure."""
        # 1. Stop old handle (best-effort)
        old_handle = self._handles.pop(language, None)
        if old_handle:
            try:
                await old_handle.stop()
            except Exception:
                pass

        # 2. Close old quiescence tracker
        old_tracker = self._quiescence_trackers.pop(language, None)
        if old_tracker:
            await old_tracker.close()

        # 3. Invalidate open file state (no didClose — handle is dead)
        await self._file_manager.invalidate_language(language)

        # 4. Clear init lock so _ensure_handle recreates
        self._initialization_locks.pop(language, None)

        # 5. Recreate via _ensure_handle
        await self._ensure_handle(language)

    async def _request_with_retry(
        self,
        file_path: str,
        method: str,
        params_builder: Callable[[LspHandle, str], dict[str, Any]],
        timeout: float | None = None,
    ) -> Any:
        """Send an LSP request with automatic retry on connection failures.

        Non-retryable errors (TimeoutError, LSP errors) propagate immediately.
        """
        if timeout is None:
            timeout = self._settings.lsp_request_timeout

        abs_path = self._resolve_file_path(file_path)
        language = self._detect_language(abs_path)
        if language is None:
            raise ValueError(f"Unsupported file type: {file_path}")

        if language not in self._restart_locks:
            self._restart_locks[language] = asyncio.Lock()

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                handle = await self._ensure_handle(language)
                uri = handle.build_uri(self._get_relative_path(abs_path))
                tracker = self._quiescence_trackers.get(language)

                async with self._file_manager.use_file(
                    handle, abs_path, uri, language, tracker=tracker
                ):
                    return await handle.send_request(
                        method, params_builder(handle, uri), timeout
                    )

            except HandleUnavailableError as e:
                last_error = e

            # Non-retryable errors (TimeoutError, LSP errors) propagate naturally

            if attempt < self._max_retries:
                logger.warning(
                    f"Retryable error on {method} (attempt {attempt + 1}): {last_error}"
                )
                async with self._restart_locks[language]:
                    await self._restart_handle(language)

        raise last_error  # type: ignore[misc]

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
        """
        if language not in self._initialization_locks:
            self._initialization_locks[language] = asyncio.Lock()

        # Ensure only one coroutine initializes the handle at a time
        async with self._initialization_locks[language]:
            if language in self._handles:
                return self._handles[language]
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
            # Create quiescence tracker BEFORE start so progress events
            # emitted during initialization are captured
            tracker = QuiescenceTracker(grace_period=self._settings.grace_period)
            self._quiescence_trackers[language] = tracker
            self._handles[language] = handle
            await handle.start()
            await handle.initialize(workspace_root=handle._workspace_root)
            # Wait for server to finish initial indexing
            await tracker.wait_for_quiescence(
                # Give a long timeout for initialization
                # Some lsp server (jdtls) requires a long time to index
                timeout=self._settings.quiescence_timeout
                * 10,
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
        """
        language = self._detect_language(file_path)
        if language is None:
            raise ValueError(f"Unsupported file type: {file_path}")
        return await self._ensure_handle(language)
