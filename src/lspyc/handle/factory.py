"""Factory classes for creating LSP handles with validation."""

import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from urllib.parse import urljoin

import websockets

from .base import LspHandle, LspStdioHandle
from .wshandle import LspWsHandle


class HandleFactory(ABC):
    """Abstract base class for LSP handle factories.

    Factories are responsible for:
    1. Validating that a handle can be created (e.g., command exists, server is reachable)
    2. Creating configured handle instances
    """

    @abstractmethod
    async def validate(self) -> tuple[bool, str | None]:
        """Check if this factory can create a valid handle.

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if the handle can be created
            - error_message: None if valid, otherwise a description of the issue
        """
        pass

    @abstractmethod
    async def create(self, workspace_root: str) -> LspHandle:
        pass


class NativeHandleFactory(HandleFactory):
    """Factory for native LSP servers via stdio.

    This factory creates handles for LSP servers that run as native processes
    and communicate via standard input/output.
    """

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Initialize the native handle factory.

        Args:
            command: Command and arguments to launch the LSP server
            cwd: Working directory for the server process
            env: Environment variables for the server process
        """
        if not command or not command[0]:
            raise ValueError("Command cannot be empty")

        self.command = command
        self.cwd = cwd
        self.env = env

    async def validate(self) -> tuple[bool, str | None]:
        """Check if the command exists in PATH.

        Returns:
            Tuple of (is_valid, error_message)
        """
        executable = self.command[0]

        # Check if command is an absolute path
        if executable.startswith("/"):
            # For absolute paths, check if file exists and is executable
            if os.path.isfile(executable) and os.access(executable, os.X_OK):
                return True, None
            else:
                return (
                    False,
                    f"Executable not found or not accessible: {executable}",
                )

        # Check if command exists in PATH
        if shutil.which(executable) is None:
            return False, f"Command '{executable}' not found in PATH"

        return True, None

    async def create(self, workspace_root: str) -> LspHandle:
        is_valid, error = await self.validate()
        if not is_valid:
            raise RuntimeError(f"Cannot create handle: {error}")

        return LspStdioHandle(
            cmd=self.command,
            cwd=self.cwd,
            env=self.env,
            workspace_root=workspace_root,
        )


class DockerHandleFactory(HandleFactory):
    """Factory for LSP servers running in Docker containers.

    This factory creates handles for LSP servers that run inside Docker
    containers with the workspace directory mounted.
    """

    def __init__(
        self,
        command: list[str],
        image: str = "lspyc-server",
        container_workspace: str = "/workspace",
    ) -> None:
        """Initialize the Docker handle factory.

        Args:
            image: Docker image name
            command: Command to run inside the container
            workspace_path: Host path to mount as workspace
            container_workspace: Container path for workspace mount
        """
        if not image:
            raise ValueError("Docker image cannot be empty")
        if not command or not command[0]:
            raise ValueError("Command cannot be empty")

        self.image = image
        self.command = command
        self.container_workspace = container_workspace

    async def validate(self) -> tuple[bool, str | None]:
        """Check if docker is available and image exists.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if docker command exists
        if shutil.which("docker") is None:
            return False, "Docker command not found in PATH"

        # Check if docker daemon is running by trying 'docker ps'
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "ps",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)

            if process.returncode != 0:
                return (
                    False,
                    f"Docker daemon not running or not accessible: {stderr.decode()}",
                )
        except asyncio.TimeoutError:
            return False, "Docker command timed out - daemon may not be running"
        except Exception as e:
            return False, f"Error checking docker daemon: {e}"

        # Check if image exists
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "images",
                "-q",
                self.image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)

            if not stdout.strip():
                return False, f"Docker image '{self.image}' not found locally"

        except asyncio.TimeoutError:
            return False, "Docker command timed out while checking image"
        except Exception as e:
            return False, f"Error checking docker image: {e}"

        return True, None

    async def create(self, workspace_root: str) -> LspHandle:
        is_valid, error = await self.validate()
        if not is_valid:
            raise RuntimeError(f"Cannot create handle: {error}")

        # Build docker run command
        docker_command = [
            "docker",
            "run",
            "-i",  # Interactive mode for stdin/stdout
            "--rm",  # Remove container after exit
            "-v",
            f"{workspace_root}:{self.container_workspace}",  # Mount workspace
            "-w",
            self.container_workspace,  # Set working directory
        ]

        # Add image and command
        docker_command.append(self.image)
        docker_command.extend(self.command)

        return LspStdioHandle(
            cmd=docker_command,
            workspace_root=self.container_workspace,
        )


class WebSocketHandleFactory(HandleFactory):
    """Factory for remote LSP servers via WebSocket.

    This factory creates handles for LSP servers that are accessed via
    WebSocket connections, typically for remote or cloud-based servers.
    """

    def __init__(
        self,
        url: str,
        server_name: str,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 10.0,
        reconnect_delay: float = 2.0,
        max_reconnect_attempts: int = -1,
    ) -> None:
        """Initialize the WebSocket handle factory.

        Args:
            url: WebSocket URL (ws:// or wss://)
            headers: Optional headers for WebSocket connection
            connect_timeout: Connection timeout in seconds
            reconnect_delay: Delay between reconnection attempts in seconds
            max_reconnect_attempts: Maximum reconnection attempts (-1 for infinite)
        """
        assert url and url.startswith(
            ("ws://", "wss://")
        ), "URL must start with ws:// or wss://"

        self.url = urljoin(url, server_name)
        self.headers = headers
        self.connect_timeout = connect_timeout
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts

    async def validate(self) -> tuple[bool, str | None]:
        """Check if WebSocket URL is reachable.

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Try to establish a test connection

            websocket = await asyncio.wait_for(
                websockets.connect(
                    self.url,
                    additional_headers=self.headers,
                ),
                timeout=self.connect_timeout,
            )

            # Close the test connection
            await websocket.close()
            return True, None

        except asyncio.TimeoutError:
            return False, f"Connection to {self.url} timed out"
        except Exception as e:
            return False, f"Failed to connect to {self.url}: {e}"

    async def create(self, workspace_root: str) -> LspHandle:
        is_valid, error = await self.validate()
        if not is_valid:
            raise RuntimeError(f"Cannot create handle: {error}")

        return LspWsHandle(
            workspace_root=workspace_root,
            url=self.url,
            headers=self.headers,
            connect_timeout=self.connect_timeout,
            reconnect_delay=self.reconnect_delay,
            max_reconnect_attempts=self.max_reconnect_attempts,
        )


class AutoHandleFactory(HandleFactory):
    """Factory for auto-detecting LSP server type and creating appropriate handle."""

    def __init__(self, candidates: list[HandleFactory]) -> None:
        super().__init__()
        self.candidates: list[HandleFactory] = candidates

    async def validate(self) -> tuple[bool, str | None]:
        for candidate in self.candidates:
            if await candidate.validate():
                return True, None
        return False, "No valid handle factory found"

    async def create(self, workspace_root: str) -> LspHandle:
        for candidate in self.candidates:
            if await candidate.validate():
                print(f"Using {candidate.__class__.__name__} handle factory")
                return await candidate.create(workspace_root)
        raise RuntimeError("No valid handle factory found")
