"""Factory builder for creating LSP handle factories from configuration.

This module provides utilities to build HandleFactory instances from various
configuration sources including environment variables, configuration files,
and programmatic specifications.

Supported configuration methods:
1. JSON environment variable: LSPYC_LANGUAGE_FACTORIES
2. Per-language environment variables: LSPYC_<LANG>_TYPE, LSPYC_<LANG>_COMMAND, etc.
3. YAML configuration file: .lspyc.yaml (searched in pwd, home, or set via LSPYC_CONFIG_FILE)
4. Fallback to DEFAULT_NATIVE_FACTORIES

Example environment variable configurations:

    # JSON format (single env var)
    LSPYC_LANGUAGE_FACTORIES='{
        "python": {"type": "native", "command": ["pyright-langserver", "--stdio"]},
        "rust": {"type": "docker", "image": "rust-analyzer", "command": ["rust-analyzer"]}
    }'

    # Per-language format (multiple env vars)
    LSPYC_PYTHON_TYPE=native
    LSPYC_PYTHON_COMMAND='["pyright-langserver", "--stdio"]'

    LSPYC_RUST_TYPE=docker
    LSPYC_RUST_DOCKER_IMAGE=rust-analyzer
    LSPYC_RUST_DOCKER_COMMAND='["rust-analyzer"]'

Example YAML configuration file (.lspyc.yaml):

    language_factories:
      python:
        type: native
        command: ["pyright-langserver", "--stdio"]
      rust:
        type: docker
        image: rust-analyzer
        command: ["rust-analyzer"]
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .handle import (
    DockerHandleFactory,
    HandleFactory,
    NativeHandleFactory,
    WebSocketHandleFactory,
)

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
    # "swift": NativeHandleFactory(["sourcekit-lsp"]),
    "kotlin": NativeHandleFactory(["kotlin-language-server"]),
}


class FactoryConfigError(Exception):
    """Raised when factory configuration is invalid."""

    pass


def build_lang_factories_from_dict(
    config: dict[str, dict[str, Any]],
) -> dict[str, HandleFactory]:
    """Internal implementation for building factories from dict."""
    factories = {}

    for language, factory_config in config.items():
        if not isinstance(factory_config, dict):
            raise FactoryConfigError(
                f"Factory config for '{language}' must be a dict, got {type(factory_config)}"
            )

        factory_type = factory_config.get("type")
        if not factory_type:
            raise FactoryConfigError(
                f"Missing 'type' field in factory config for '{language}'"
            )

        try:
            if factory_type == "native":
                factories[language] = _build_native_factory(factory_config)
            elif factory_type == "docker":
                factories[language] = _build_docker_factory(factory_config)
            elif factory_type == "websocket":
                factories[language] = _build_websocket_factory(factory_config)
            else:
                raise FactoryConfigError(
                    f"Unknown factory type '{factory_type}' for '{language}'. "
                    f"Must be 'native', 'docker', or 'websocket'."
                )
        except Exception as e:
            raise FactoryConfigError(
                f"Failed to build factory for '{language}': {e}"
            ) from e

    return factories


def build_lang_factories_from_file(config_file: Path) -> dict[str, HandleFactory]:
    """Build factories from a YAML configuration file.

    Args:
        config_file: Path to YAML configuration file

    Returns:
        Dictionary mapping language IDs to HandleFactory instances

    Raises:
        FactoryConfigError: If file cannot be read or is invalid

    Example YAML format:
        language_factories:
          python:
            type: native
            command: ["pyright-langserver", "--stdio"]
          rust:
            type: docker
            image: rust-analyzer
            command: ["rust-analyzer"]
    """
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        raise FactoryConfigError(
            f"Failed to read config file {config_file}: {e}"
        ) from e

    if not isinstance(data, dict):
        raise FactoryConfigError(
            f"Config file {config_file} must contain a YAML dictionary"
        )

    # Look for 'language_factories' key
    config = data.get("language_factories")
    if not config:
        raise FactoryConfigError(
            f"Config file {config_file} must contain 'language_factories' key"
        )

    if not isinstance(config, dict):
        raise FactoryConfigError(
            f"'language_factories' in {config_file} must be a dictionary"
        )

    return build_lang_factories_from_dict(config)


def build_language_factories_default() -> dict[str, HandleFactory]:
    """Build language factories from environment configuration.

    Checks configuration sources in the following order:
    1. LSPYC_LANGUAGE_FACTORIES (JSON format)
    2. Per-language environment variables (LSPYC_<LANG>_TYPE, etc.)
    3. Configuration file (.lspyc.yaml in pwd, home, or path from LSPYC_CONFIG_FILE)
    4. DEFAULT_NATIVE_FACTORIES

    Returns:
        Dictionary mapping language IDs to HandleFactory instances

    Raises:
        FactoryConfigError: If configuration is invalid
    """
    # Try JSON env var first
    json_config = os.environ.get("LSPYC_LANGUAGE_FACTORIES")
    if json_config:
        try:
            config_dict = json.loads(json_config)
            return build_lang_factories_from_dict(config_dict)
        except json.JSONDecodeError as e:
            raise FactoryConfigError(
                f"Invalid JSON in LSPYC_LANGUAGE_FACTORIES: {e}"
            ) from e

    # Try per-language env vars
    factories = _build_factories_from_per_language_env()
    if factories:
        return factories

    # Try config file
    config_file = _find_config_file()
    if config_file:
        return build_lang_factories_from_file(config_file)

    return DEFAULT_NATIVE_FACTORIES.copy()


def _build_native_factory(config: dict[str, Any]) -> NativeHandleFactory:
    """Build a NativeHandleFactory from configuration."""
    command = config.get("command")
    if not command:
        raise FactoryConfigError("Native factory requires 'command' field")

    if isinstance(command, str):
        # Parse as JSON if string
        try:
            command = json.loads(command)
        except json.JSONDecodeError:
            # Treat as single command
            command = [command]

    if not isinstance(command, list):
        raise FactoryConfigError(
            f"'command' must be a list or JSON string, got {type(command)}"
        )

    cwd = config.get("cwd")
    env = config.get("env")

    return NativeHandleFactory(command=command, cwd=cwd, env=env)


def _build_docker_factory(config: dict[str, Any]) -> DockerHandleFactory:
    """Build a DockerHandleFactory from configuration."""
    image = config.get("image")
    if not image:
        raise FactoryConfigError("Docker factory requires 'image' field")

    command = config.get("command")
    if not command:
        raise FactoryConfigError("Docker factory requires 'command' field")

    if isinstance(command, str):
        try:
            command = json.loads(command)
        except json.JSONDecodeError:
            command = [command]

    if not isinstance(command, list):
        raise FactoryConfigError(
            f"'command' must be a list or JSON string, got {type(command)}"
        )

    container_workspace = config.get("container_workspace", "/workspace")
    additional_mounts = config.get("additional_mounts", None)

    return DockerHandleFactory(
        image=image,
        command=command,
        container_workspace=container_workspace,
        additional_mounts=additional_mounts,
    )


def _build_websocket_factory(config: dict[str, Any]) -> WebSocketHandleFactory:
    """Build a WebSocketHandleFactory from configuration."""
    url = config.get("url")
    if not url:
        raise FactoryConfigError("WebSocket factory requires 'url' field")

    headers = config.get("headers")
    connect_timeout = config.get("connect_timeout", 10.0)
    reconnect_delay = config.get("reconnect_delay", 2.0)
    max_reconnect_attempts = config.get("max_reconnect_attempts", -1)

    return WebSocketHandleFactory(
        url=url,
        headers=headers,
        connect_timeout=connect_timeout,
        reconnect_delay=reconnect_delay,
        max_reconnect_attempts=max_reconnect_attempts,
    )


def _build_factories_from_per_language_env() -> dict[str, HandleFactory]:
    """Build factories from per-language environment variables.

    Looks for patterns like:
    - LSPYC_PYTHON_TYPE=native
    - LSPYC_PYTHON_COMMAND='["pyright-langserver", "--stdio"]'
    - LSPYC_RUST_TYPE=docker
    - LSPYC_RUST_DOCKER_IMAGE=rust-analyzer
    """
    factories = {}
    prefix = "LSPYC_"

    # Find all languages with _TYPE env vars
    languages = set()
    for key in os.environ:
        if key.startswith(prefix) and key.endswith("_TYPE"):
            # Extract language name (e.g., LSPYC_PYTHON_TYPE -> python)
            language = key[len(prefix) : -len("_TYPE")].lower()
            languages.add(language)

    # Build factory for each language
    for language in languages:
        lang_prefix = f"{prefix}{language.upper()}_"
        factory_type = os.environ.get(f"{lang_prefix}TYPE")

        if not factory_type:
            continue

        try:
            if factory_type == "native":
                config = {
                    "type": "native",
                    "command": os.environ.get(f"{lang_prefix}COMMAND"),
                    "cwd": os.environ.get(f"{lang_prefix}CWD"),
                    "env": os.environ.get(f"{lang_prefix}ENV"),
                }
                factories[language] = _build_native_factory(config)

            elif factory_type == "docker":
                config = {
                    "type": "docker",
                    "image": os.environ.get(f"{lang_prefix}DOCKER_IMAGE"),
                    "command": os.environ.get(f"{lang_prefix}DOCKER_COMMAND"),
                    "container_workspace": os.environ.get(
                        f"{lang_prefix}DOCKER_CONTAINER_WORKSPACE", "/workspace"
                    ),
                    "additional_mounts": os.environ.get(
                        f"{lang_prefix}DOCKER_ADDITIONAL_MOUNTS"
                    ),
                }
                factories[language] = _build_docker_factory(config)

            elif factory_type == "websocket":
                config = {
                    "type": "websocket",
                    "url": os.environ.get(f"{lang_prefix}WEBSOCKET_URL"),
                    "headers": os.environ.get(f"{lang_prefix}WEBSOCKET_HEADERS"),
                    "connect_timeout": float(
                        os.environ.get(
                            f"{lang_prefix}WEBSOCKET_CONNECT_TIMEOUT", "10.0"
                        )
                    ),
                    "reconnect_delay": float(
                        os.environ.get(f"{lang_prefix}WEBSOCKET_RECONNECT_DELAY", "2.0")
                    ),
                    "max_reconnect_attempts": int(
                        os.environ.get(
                            f"{lang_prefix}WEBSOCKET_MAX_RECONNECT_ATTEMPTS", "-1"
                        )
                    ),
                }
                factories[language] = _build_websocket_factory(config)

        except Exception as e:
            raise FactoryConfigError(
                f"Failed to build factory for '{language}' from env vars: {e}"
            ) from e

    return factories


def _find_config_file() -> Path | None:
    """Find configuration file in standard locations.

    Searches for .lspyc.yaml in the following order:
    1. Path specified in LSPYC_CONFIG_FILE environment variable
    2. .lspyc.yaml in current working directory
    3. .lspyc.yaml in home directory

    Returns:
        Path to config file, or None if not found
    """
    # Check environment variable first
    env_path = os.environ.get("LSPYC_CONFIG_FILE")
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if path.exists() and path.is_file():
            return path
        else:
            raise FactoryConfigError(
                f"Config file specified in LSPYC_CONFIG_FILE not found: {env_path}"
            )

    # Search in standard locations
    candidates = [
        Path.cwd() / ".lspyc.yaml",
        Path.home() / ".lspyc.yaml",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    return None
