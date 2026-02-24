"""Configuration settings for lspyc."""

from typing import Literal, TypeAlias

from pydantic import model_validator
from pydantic_settings import BaseSettings

FactoryTypes: TypeAlias = Literal["native", "docker", "ws", "auto", "file"]


class WsHandleSettings(BaseSettings):
    base_url: str = "ws://localhost:8080"
    connect_timeout: float = 10.0
    reconnect_delay: float = 1.0
    max_reconnect_attempts: int = -1
    local_mount_prefix: str | None = None
    remote_mount_prefix: str | None = None

    @model_validator(mode="after")
    def _check_mount_prefixes(self) -> "WsHandleSettings":
        if (self.local_mount_prefix is None) != (self.remote_mount_prefix is None):
            raise ValueError(
                "local_mount_prefix and remote_mount_prefix must both be set or both be None"
            )
        return self


class DockerHandleSettings(BaseSettings):
    image: str = "lspyc-server"
    container_workspace: str = "/workspace"


class LspycSettings(BaseSettings):
    """Settings for LSP client configuration.

    All settings can be overridden via environment variables with the LSPYC_ prefix.
    For example, set LSPYC_MAX_OPEN_FILES=50 to change max_open_files.
    """

    max_open_files: int = 20
    """Maximum number of files to keep open concurrently"""

    grace_period: float = 1.0
    """Quiescence tracker grace period in seconds"""

    quiescence_timeout: float = 10.0
    """Timeout for wait_for_quiescence operations in seconds"""

    lsp_request_timeout: float = 10.0
    """Timeout for LSP requests (documentSymbol, definition, references, shutdown) in seconds"""

    model_config = {
        "env_prefix": "LSPYC_",
        "case_sensitive": False,
        "env_nested_delimiter": "__",
    }

    factory_ty: FactoryTypes = "auto"

    docker_factory: DockerHandleSettings = DockerHandleSettings()

    ws_factory: WsHandleSettings = WsHandleSettings()

    factory_file: str | None = None
