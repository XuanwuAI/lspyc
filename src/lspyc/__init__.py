"""LSP Python Client - A library for LSP server management and usage."""

from .factory_builder import (
    build_lang_factories_from_dict,
    build_lang_factories_from_file,
)
from .handle import (
    DockerHandleFactory,
    HandleFactory,
    LspHandle,
    LspStdioHandle,
    LspWsHandle,
    NativeHandleFactory,
    WebSocketHandleFactory,
)
from .handle.protocol import DocumentSymbol, Location
from .mlclient import MutilLangClient
from .settings import LspycSettings

__version__ = "0.1.0"
__all__ = [
    "DocumentSymbol",
    "Location",
    "HandleFactory",
    "DockerHandleFactory",
    "NativeHandleFactory",
    "WebSocketHandleFactory",
    "LspHandle",
    "LspStdioHandle",
    "LspWsHandle",
    "LspycSettings",
    "MutilLangClient",
    "build_lang_factories_from_dict",
    "build_lang_factories_from_file",
]
