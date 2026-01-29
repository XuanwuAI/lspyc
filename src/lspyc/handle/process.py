"""Process management utilities for LSP servers."""

import asyncio
from asyncio.subprocess import Process
from enum import Enum
from typing import Awaitable, Callable


class ServerState(Enum):
    """LSP server process states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class ProcessManager:
    """Manages an LSP server subprocess with async I/O."""

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Initialize the process manager."""
        self.command = command
        self.cwd = cwd
        self.env = env
        self.process: Process | None = None
        self.state = ServerState.STOPPED
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._shutdown_event: asyncio.Event | None = None

    async def start(
        self,
        on_stdout: Callable[[bytes], Awaitable[None]],
        on_stderr: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """Start the server process."""
        if self.state != ServerState.STOPPED:
            raise RuntimeError(f"Cannot start server in state: {self.state}")

        self.state = ServerState.STARTING
        self._shutdown_event = asyncio.Event()

        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
            )

            # Start reading stdout and stderr
            self._read_task = asyncio.create_task(
                self._read_stream(self.process.stdout, on_stdout, "stdout")
            )
            self._stderr_task = asyncio.create_task(
                self._read_stream(self.process.stderr, on_stderr, "stderr")
            )

            self.state = ServerState.RUNNING

        except Exception as e:
            self.state = ServerState.STOPPED
            raise OSError(f"Failed to start server: {e}") from e

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the server process gracefully."""
        if self.state not in (ServerState.RUNNING, ServerState.STARTING):
            raise RuntimeError(f"Cannot stop server in state: {self.state}")

        self.state = ServerState.STOPPING

        if self.process is None:
            self._cleanup()
            return

        try:
            # Signal readers to stop
            if self._shutdown_event:
                self._shutdown_event.set()

            # Close stdin first to signal EOF to the server
            if self.process.stdin:
                self.process.stdin.close()
                try:
                    await asyncio.wait_for(
                        self.process.stdin.wait_closed(), timeout=1.0
                    )
                except (asyncio.TimeoutError, ConnectionResetError):
                    pass

            # Try graceful termination
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()

        except ProcessLookupError:
            pass  # Already gone
        finally:
            await self._cleanup_tasks()
            self._cleanup()

    async def _cleanup_tasks(self) -> None:
        """Cancel and await reader tasks with timeout."""
        tasks: list[asyncio.Task[None]] = []
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            tasks.append(self._read_task)
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            tasks.append(self._stderr_task)

        if tasks:
            # Give tasks a moment to clean up, but don't block forever
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=2.0
                )
            except asyncio.TimeoutError:
                # Force cancel if still running
                for t in tasks:
                    if not t.done():
                        t.cancel()

    def _cleanup(self) -> None:
        """Synchronous cleanup."""
        self.process = None
        self._read_task = None
        self._stderr_task = None
        self._shutdown_event = None
        self.state = ServerState.STOPPED

    async def write(self, data: bytes) -> None:
        """Write data to the server's stdin."""
        if self.state != ServerState.RUNNING or self.process is None:
            raise RuntimeError(f"Cannot write to server in state: {self.state}")

        if self.process.stdin is None:
            raise RuntimeError("Server stdin is not available")

        try:
            self.process.stdin.write(data)
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise RuntimeError("Server stdin closed") from e

    @property
    def is_running(self) -> bool:
        """Check if the process is currently running."""
        return self.state == ServerState.RUNNING and self.process is not None

    @property
    def exit_code(self) -> int | None:
        """Get the exit code if the process has terminated."""
        return self.process.returncode if self.process else None

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        callback: Callable[[bytes], Awaitable[None]],
        name: str,  # for debugging
    ) -> None:
        """Read from a stream and invoke callback with data."""
        if stream is None:
            return

        try:
            while True:
                # Check for shutdown signal
                if self._shutdown_event and self._shutdown_event.is_set():
                    # Try to drain remaining data
                    try:
                        data = await asyncio.wait_for(stream.read(4096), timeout=0.5)
                        if data:
                            await callback(data)
                    except asyncio.TimeoutError:
                        pass
                    break

                # Use wait_for to allow periodic shutdown checks
                try:
                    data = await asyncio.wait_for(stream.read(4096), timeout=0.1)
                except asyncio.TimeoutError:
                    continue  # Loop back to check shutdown_event

                if not data:
                    break

                await callback(data)

        except asyncio.CancelledError:
            # Normal cancellation during shutdown
            raise  # Re-raise to properly signal cancellation
        except Exception as e:
            # Log unexpected errors but don't crash
            pass
