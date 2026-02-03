"""Configuration settings for lspyc."""

from pydantic_settings import BaseSettings


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
    }
