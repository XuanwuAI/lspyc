"""LSP Python Client - A library for LSP server management and usage."""

from .factory_builder import (
    build_lang_factories_from_dict,
    build_lang_factories_from_file,
)
from .handle import (
    DockerHandleFactory,
    HandleFactory,
    HandleUnavailableError,
    LspHandle,
    LspTransport,
    NativeHandleFactory,
    StdioTransport,
    WebSocketHandleFactory,
    WsTransport,
)
from .handle.protocol import DocumentSymbol, Location
from .mlclient import MutilLangClient
from .settings import LspycSettings
from .threaded import ThreadedClient

__version__ = "0.1.0"
__all__ = [
    "DocumentSymbol",
    "HandleUnavailableError",
    "Location",
    "HandleFactory",
    "DockerHandleFactory",
    "NativeHandleFactory",
    "WebSocketHandleFactory",
    "LspHandle",
    "LspTransport",
    "StdioTransport",
    "WsTransport",
    "LspycSettings",
    "MutilLangClient",
    "ThreadedClient",
    "build_lang_factories_from_dict",
    "build_lang_factories_from_file",
]
