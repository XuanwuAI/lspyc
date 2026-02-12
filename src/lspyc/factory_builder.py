"""Factory builder for creating LSP handle factories from configuration.

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

from pathlib import Path
from typing import Any

import yaml

from .handle import (
    AutoHandleFactory,
    DockerHandleFactory,
    HandleFactory,
    NativeHandleFactory,
    WebSocketHandleFactory,
)
from .settings import DockerHandleSettings, FactoryTypes, WsHandleSettings

# Default native LSP server factories for supported languages
# Users can use this as a starting point or create their own custom mappings
DEFAULT_NATIVE_FACTORIES: dict[str, NativeHandleFactory] = {
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


def build_factories(
    ty: FactoryTypes = "auto",
    docker_conf: DockerHandleSettings | None = None,
    ws_conf: WsHandleSettings | None = None,
    factory_conf_file: str | None = None,
) -> dict[str, HandleFactory]:
    print(f"Building factories with type {ty}")
    if ty == "native":
        return DEFAULT_NATIVE_FACTORIES  # type: ignore
    if ty == "docker":
        assert docker_conf is not None
        return {
            lang: DockerHandleFactory(factory.command, **docker_conf.model_dump())
            for lang, factory in DEFAULT_NATIVE_FACTORIES.items()
        }
    if ty == "ws":
        assert ws_conf is not None
        return {
            lang: WebSocketHandleFactory(ws_conf.base_url, factory.command[0])
            for lang, factory in DEFAULT_NATIVE_FACTORIES.items()
        }
    if ty == "auto":
        m = {}
        for lang, factory in DEFAULT_NATIVE_FACTORIES.items():
            candidates: list[HandleFactory] = [factory]
            if docker_conf is not None:
                candidates.append(
                    DockerHandleFactory(
                        factory.command,
                        docker_conf.image,
                        docker_conf.container_workspace,
                    )
                )
            if ws_conf:
                candidates.append(
                    WebSocketHandleFactory(ws_conf.base_url, factory.command[0]),
                )
            m[lang] = AutoHandleFactory(candidates)
        return m
    assert ty == "file"
    conf_file = Path(factory_conf_file) if factory_conf_file else _find_config_file()
    assert conf_file is not None
    return build_lang_factories_from_file(conf_file)


def build_lang_factories_from_dict(
    config: dict[str, dict[str, Any]],
) -> dict[str, HandleFactory]:
    """Internal implementation for building factories from dict."""
    factories = {}

    for language, factory_config in config.items():
        assert isinstance(factory_config, dict)
        factory_type = factory_config.get("type")
        assert factory_type in ["native", "docker", "ws"]
        if factory_type == "native":
            factories[language] = NativeHandleFactory(**factory_config)
        elif factory_type == "docker":
            factories[language] = DockerHandleFactory(**factory_config)
        elif factory_type == "ws":
            factories[language] = WebSocketHandleFactory(**factory_config)
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
    with open(config_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    # Look for 'language_factories' key
    config = data.get("language_factories")
    assert isinstance(config, dict)
    return build_lang_factories_from_dict(config)


def _find_config_file() -> Path | None:
    """Find configuration file in standard locations.

    Searches for .lspyc.yaml in the following order:
    1. Path specified in LSPYC_CONFIG_FILE environment variable
    2. .lspyc.yaml in current working directory
    3. .lspyc.yaml in home directory

    Returns:
        Path to config file, or None if not found
    """
    # Search in standard locations
    candidates = [
        Path.cwd() / ".lspyc.yaml",
        Path.home() / ".lspyc.yaml",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    return None
