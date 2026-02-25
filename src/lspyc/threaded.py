"""Threaded wrapper for MutilLangClient. Used to provide a synchronous API.

Provides a ThreadedClient that owns a dedicated background thread with a persistent
event loop, ensuring all LSP operations (handle creation, queries, shutdown) happen
on the same loop for their entire lifetime.
"""

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Coroutine, Mapping, TypeVar

from .handle import HandleFactory
from .handle.protocol import DocumentSymbol, Location
from .mlclient import MutilLangClient
from .settings import LspycSettings

T = TypeVar("T")


class ThreadedClient:
    """Sync/async wrapper around MutilLangClient with a dedicated background event loop.

    All LSP operations run on a single persistent event loop in a daemon thread,
    ensuring handles and reader tasks are never orphaned by loop closure.

    Usage (sync):
        client = ThreadedClient("/path/to/workspace")
        symbols = client.get_document_symbols("src/main.py")
        client.shutdown()

    Usage (async):
        client = ThreadedClient("/path/to/workspace")
        symbols = await client.aget_document_symbols("src/main.py")
        await client.ashutdown()

    Usage (context manager):
        with ThreadedClient("/path/to/workspace") as client:
            symbols = client.get_document_symbols("src/main.py")
    """

    def __init__(
        self,
        workspace_root: str,
        language_factories: Mapping[str, HandleFactory] | None = None,
        settings: LspycSettings = LspycSettings(),
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="lspyc-event-loop"
        )
        self._thread.start()
        self._inner = MutilLangClient(workspace_root, language_factories, settings)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def inner(self) -> MutilLangClient:
        """Direct access to the underlying MutilLangClient.

        Only use this from coroutines submitted via run_sync/run_async,
        which already execute on the background loop.
        """
        return self._inner

    # ── Generic coroutine submission ──────────────────────────────────

    def run_sync(self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit a coroutine to the background loop and block until it completes.

        Args:
            coro: The coroutine to execute on the background loop.

        Returns:
            The coroutine's return value.
        """
        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    async def run_async(self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit a coroutine to the background loop and await the result.

        Use this from an async caller that is NOT on the background loop.

        Args:
            coro: The coroutine to execute on the background loop.

        Returns:
            The coroutine's return value.
        """
        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

    # ── Sync API ──────────────────────────────────────────────────────

    def get_document_symbols(self, file_path: str) -> list[DocumentSymbol]:
        return self.run_sync(self._inner.get_document_symbols(file_path))

    def get_definition(
        self, file_path: str, line: int, character: int
    ) -> list[Location]:
        return self.run_sync(self._inner.get_definition(file_path, line, character))

    def get_references(
        self,
        file_path: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[Location]:
        return self.run_sync(
            self._inner.get_references(file_path, line, character, include_declaration)
        )

    def shutdown(self) -> None:
        """Shutdown the inner client, stop the background loop, and join the thread."""
        if not self._loop.is_running():
            return
        try:
            self.run_sync(self._inner.shutdown())
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    # ── Async API ─────────────────────────────────────────────────────

    async def aget_document_symbols(self, file_path: str) -> list[DocumentSymbol]:
        return await self.run_async(self._inner.get_document_symbols(file_path))

    async def aget_definition(
        self, file_path: str, line: int, character: int
    ) -> list[Location]:
        return await self.run_async(
            self._inner.get_definition(file_path, line, character)
        )

    async def aget_references(
        self,
        file_path: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[Location]:
        return await self.run_async(
            self._inner.get_references(file_path, line, character, include_declaration)
        )

    async def ashutdown(self) -> None:
        """Async shutdown: shut down inner client, stop loop, join thread."""
        if not self._loop.is_running():
            return
        try:
            await self.run_async(self._inner.shutdown())
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    # ── Context manager support ───────────────────────────────────────

    def __enter__(self) -> "ThreadedClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    async def __aenter__(self) -> "ThreadedClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.ashutdown()
