"""Stdio transport for LSP servers via subprocess."""

import asyncio
from asyncio.subprocess import Process
from typing import Awaitable, Callable

from .base import HandleUnavailableError, LspTransport


class StdioTransport(LspTransport):
    """Transport that spawns a subprocess and communicates via stdin/stdout."""

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = env
        self._process: Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._shutdown_event: asyncio.Event | None = None

    async def start(
        self,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        assert self._process is None or self._process.returncode is None
        self._shutdown_event = asyncio.Event()

        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=self._env,
            )

            self._read_task = asyncio.create_task(
                self._read_stream(self._process.stdout, on_data, on_close)
            )
            self._stderr_task = asyncio.create_task(
                self._read_stderr(self._process.stderr)
            )
        except Exception as e:
            self._cleanup()
            raise OSError(f"Failed to start server: {e}") from e

    async def stop(self, timeout: float = 5.0) -> None:
        if self._process is None:
            return

        try:
            if self._shutdown_event:
                self._shutdown_event.set()

            # Close stdin to signal EOF
            if self._process.stdin:
                self._process.stdin.close()
                try:
                    await asyncio.wait_for(
                        self._process.stdin.wait_closed(), timeout=1.0
                    )
                except (asyncio.TimeoutError, ConnectionResetError):
                    pass

            # Graceful termination
            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()

        except ProcessLookupError:
            pass
        finally:
            await self._cleanup_tasks()
            self._cleanup()

    async def write(self, data: bytes) -> None:
        if self._process is None or self._process.stdin is None:
            raise HandleUnavailableError("Stdio transport is not running")

        try:
            self._process.stdin.write(data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise HandleUnavailableError("Stdio pipe broken")

    # --- Internal ---

    def _cleanup(self) -> None:
        self._process = None
        self._read_task = None
        self._stderr_task = None
        self._shutdown_event = None

    async def _cleanup_tasks(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            tasks.append(self._read_task)
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            tasks.append(self._stderr_task)

        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=2.0
                )
            except asyncio.TimeoutError:
                for t in tasks:
                    if not t.done():
                        t.cancel()

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        if stream is None:
            return

        try:
            while True:
                if self._shutdown_event and self._shutdown_event.is_set():
                    try:
                        data = await asyncio.wait_for(stream.read(4096), timeout=0.5)
                        if data:
                            await on_data(data)
                    except asyncio.TimeoutError:
                        pass
                    break

                try:
                    data = await asyncio.wait_for(stream.read(4096), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if not data:
                    break

                await on_data(data)

        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            on_close()

    async def _read_stderr(
        self,
        stream: asyncio.StreamReader | None,
    ) -> None:
        """Drain stderr to prevent buffer deadlock."""
        if stream is None:
            return
        try:
            while True:
                if self._shutdown_event and self._shutdown_event.is_set():
                    break
                try:
                    data = await asyncio.wait_for(stream.read(4096), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
