"""JSON-RPC protocol handling for LSP communication."""

import json
from typing import Any, NotRequired, TypedDict


class Position(TypedDict):
    """Position in a text document expressed as zero-based line and character offset."""

    line: int
    character: int


class Range(TypedDict):
    """A range in a text document expressed as (zero-based) start and end positions."""

    start: Position
    end: Position


class Location(TypedDict):
    """Represents a location inside a resource, such as a line inside a text file."""

    uri: str
    range: Range


class DocumentSymbol(TypedDict):
    """Represents programming constructs like variables, classes, interfaces etc."""

    name: str
    detail: NotRequired[str]
    kind: int  # SymbolKind enum value
    tags: NotRequired[list[int]]  # SymbolTag enum values
    deprecated: NotRequired[bool]
    range: Range
    selectionRange: Range
    children: NotRequired[list["DocumentSymbol"]]


class JsonRpcMessage:
    """Represents a JSON-RPC message."""

    def __init__(self, content: dict[str, Any]) -> None:
        """Initialize a JSON-RPC message.

        Args:
            content: The JSON-RPC message content
        """
        self.content = content

    @property
    def is_request(self) -> bool:
        """Check if this is a request message."""
        return "method" in self.content and "id" in self.content

    @property
    def is_response(self) -> bool:
        """Check if this is a response message."""
        return "id" in self.content and (
            "result" in self.content or "error" in self.content
        )

    @property
    def is_notification(self) -> bool:
        """Check if this is a notification message."""
        return "method" in self.content and "id" not in self.content

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return self.content


def encode_message(message: dict[str, Any]) -> bytes:
    """Encode a JSON-RPC message with Content-Length header.

    Args:
        message: The JSON-RPC message to encode

    Returns:
        The encoded message with headers
    """
    content = json.dumps(message, separators=(",", ":"))
    content_bytes = content.encode("utf-8")
    header = f"Content-Length: {len(content_bytes)}\r\n\r\n"
    return header.encode("ascii") + content_bytes


def decode_header(data: bytes) -> tuple[int | None, int]:
    """Decode the Content-Length header from message data.

    Args:
        data: The raw message data

    Returns:
        Tuple of (content_length, header_end_position) or (None, 0) if incomplete
    """
    try:
        # Find the header separator
        separator = b"\r\n\r\n"
        separator_idx = data.find(separator)

        if separator_idx == -1:
            return None, 0

        # Parse headers
        header_data = data[:separator_idx].decode("ascii")
        content_length = None

        for line in header_data.split("\r\n"):
            if line.startswith("Content-Length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break

        if content_length is None:
            raise ValueError("Missing Content-Length header")

        return content_length, separator_idx + len(separator)

    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"Invalid message header: {e}") from e


def decode_message(data: bytes) -> tuple[JsonRpcMessage | None, bytes]:
    """Decode a JSON-RPC message from raw data.

    Args:
        data: The raw message data

    Returns:
        Tuple of (decoded_message, remaining_data) or (None, data) if incomplete

    Raises:
        ValueError: If the message format is invalid
    """
    if not data:
        return None, data

    # Parse header
    content_length, header_end = decode_header(data)

    if content_length is None:
        # Incomplete header
        return None, data

    # Check if we have the complete message
    message_end = header_end + content_length
    if len(data) < message_end:
        # Incomplete message
        return None, data

    # Extract and parse content
    content_bytes = data[header_end:message_end]
    remaining = data[message_end:]

    try:
        content = json.loads(content_bytes.decode("utf-8"))
        return JsonRpcMessage(content), remaining
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Invalid JSON-RPC message: {e}") from e
