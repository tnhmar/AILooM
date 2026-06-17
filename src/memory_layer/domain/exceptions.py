"""Exception hierarchy for memory-layer — all errors derive from MemoryLayerError."""

from __future__ import annotations


class MemoryLayerError(Exception):
    """Base exception for all memory-layer errors."""


# ---------------------------------------------------------------------------
# Tenant / access
# ---------------------------------------------------------------------------

class TenantIsolationViolation(MemoryLayerError):
    """Raised when an actor attempts to access a tenant it is not authorised for."""

    def __init__(self, actor: str, requested_tenant_id: str) -> None:
        self.actor = actor
        self.requested_tenant_id = requested_tenant_id
        super().__init__(
            f"Tenant isolation violation: actor={actor}, requested={requested_tenant_id}"
        )


# ---------------------------------------------------------------------------
# Record / fact lookups
# ---------------------------------------------------------------------------

class MemoryNotFoundError(MemoryLayerError):
    """Raised when a requested MemoryRecord does not exist."""

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        super().__init__(f"Memory not found: {memory_id}")


class FactNotFoundError(MemoryLayerError):
    """Raised when a requested Fact does not exist."""


class IdempotencyConflictError(MemoryLayerError):
    """Raised when a write is rejected due to an idempotency key conflict."""


# ---------------------------------------------------------------------------
# Capability / feature flags
# ---------------------------------------------------------------------------

class CapabilityNotAvailableError(MemoryLayerError):
    """Raised when a requested capability (e.g. graph) is not available in this deployment."""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"Capability not available: {capability}")


# ---------------------------------------------------------------------------
# Engine errors
# ---------------------------------------------------------------------------

class ExtractionError(MemoryLayerError):
    """Raised when LLM-backed fact extraction fails or returns an unparseable response."""


class StorageError(MemoryLayerError):
    """Raised when a storage adapter encounters an unrecoverable error."""


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------

class SchemaVersionError(MemoryLayerError):
    """Raised when the on-disk schema version does not match the expected version."""

    def __init__(self, expected: int, found: int) -> None:
        self.expected = expected
        self.found = found
        super().__init__(f"Schema version mismatch: expected={expected}, found={found}")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class PolicyError(MemoryLayerError):
    """Raised when a lifecycle policy evaluation fails or produces an invalid result."""
