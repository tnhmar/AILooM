"""SDK-level error hierarchy for memory-layer clients."""

from __future__ import annotations

from typing import Any


class MemoryLayerClientError(Exception):
    """Base exception for all memory-layer SDK errors."""


class MemoryLayerHTTPError(MemoryLayerClientError):
    """Raised when the server returns a non-2xx response.

    Attributes
    ----------
    status_code:
        HTTP status code returned by the server.
    error_code:
        Machine-readable error code from the response body (e.g. ``MEMORY_NOT_FOUND``).
    message:
        Human-readable message from the response body.
    details:
        Arbitrary detail dict from the response body.
    """

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details: dict[str, Any] = details or {}
        super().__init__(f"HTTP {status_code} [{error_code}]: {message}")


class MemoryLayerTransportError(MemoryLayerClientError):
    """Raised when a transport-level failure occurs (network, timeout, etc.).

    Wraps the underlying ``httpx.HTTPError`` as ``__cause__``.
    """
