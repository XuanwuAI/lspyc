"""Stdio transport for LSP servers via subprocess."""

import asyncio
import logging
from asyncio.subprocess import Process
from typing import Awaitable, Callable

from .base import HandleUnavailableError, LspTransport

logger = logging.getLogger(__name__)


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

    async def start(
        self,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        assert self._process is None or self._process.returncode is None

        logger.info(f"Starting stdio transport: {' '.join(self._command)}")
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

    async def stop(self, timeout: float = 5.0) -> None:
        if self._process is None:
            return

        try:
            if self._process.stdin:
                self._process.stdin.close()

            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=timeout)
                    logger.info(f"Stdio transport stopped gracefully: {self._command[0]}")
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
                    logger.info(f"Stdio transport force-killed: {self._command[0]}")
            else:
                logger.info(f"Stdio transport already exited: {self._command[0]}")
        except ProcessLookupError:
            logger.info(f"Stdio transport already exited: {self._command[0]}")
        finally:
            await self._cleanup_tasks()
            self._process = None

    async def write(self, data: bytes) -> None:
        if self._process is None or self._process.stdin is None:
            raise HandleUnavailableError("Stdio transport is not running")

        try:
            self._process.stdin.write(data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise HandleUnavailableError("Stdio pipe broken")

    # --- Internal ---

    async def _cleanup_tasks(self) -> None:
        tasks = [t for t in (self._read_task, self._stderr_task) if t and not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._read_task = None
        self._stderr_task = None

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        on_data: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], None],
    ) -> None:
        if stream is None:
            return
        try:
            while data := await stream.read(4096):
                await on_data(data)
        finally:
            on_close()

    async def _read_stderr(
        self,
        stream: asyncio.StreamReader | None,
    ) -> None:
        """Drain stderr to prevent buffer deadlock."""
        if stream is None:
            return
        while await stream.read(4096):
            pass
