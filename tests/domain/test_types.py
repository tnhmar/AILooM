"""Tests for domain value types, factory functions, enums, and exception hierarchy."""

from __future__ import annotations

import pytest

from memory_layer.domain.exceptions import (
    CapabilityNotAvailableError,
    ExtractionError,
    FactNotFoundError,
    IdempotencyConflictError,
    MemoryLayerError,
    MemoryNotFoundError,
    PolicyError,
    SchemaVersionError,
    StorageError,
    TenantIsolationViolation,
)
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PipelineStatus,
    new_audit_id,
    new_fact_id,
    new_job_id,
    new_memory_id,
    new_policy_id,
    new_schedule_id,
    new_tenant_id,
    new_trace_id,
)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


class TestFactories:
    def test_new_memory_id_is_unique(self) -> None:
        assert new_memory_id() != new_memory_id()

    def test_new_tenant_id_is_unique(self) -> None:
        assert new_tenant_id() != new_tenant_id()

    def test_new_fact_id_is_unique(self) -> None:
        assert new_fact_id() != new_fact_id()

    def test_new_trace_id_is_unique(self) -> None:
        assert new_trace_id() != new_trace_id()

    def test_new_audit_id_is_unique(self) -> None:
        assert new_audit_id() != new_audit_id()

    def test_new_policy_id_is_unique(self) -> None:
        assert new_policy_id() != new_policy_id()

    def test_new_schedule_id_is_unique(self) -> None:
        assert new_schedule_id() != new_schedule_id()

    def test_new_job_id_is_unique(self) -> None:
        assert new_job_id() != new_job_id()

    def test_ids_are_strings(self) -> None:
        assert isinstance(new_memory_id(), str)
        assert isinstance(new_tenant_id(), str)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestLifecycleState:
    def test_members(self) -> None:
        members = {s.value for s in LifecycleState}
        assert "ACTIVE" in members
        assert "ARCHIVED" in members
        assert "DELETED" in members

    def test_is_str(self) -> None:
        assert isinstance(LifecycleState.ACTIVE, str)
        assert LifecycleState.ACTIVE == "ACTIVE"


class TestMemorySector:
    def test_members(self) -> None:
        members = {s.value for s in MemorySector}
        assert "EPISODIC" in members
        assert "SEMANTIC" in members
        assert "PROCEDURAL" in members


class TestPipelineStatus:
    def test_members(self) -> None:
        members = {s.value for s in PipelineStatus}
        assert "PENDING" in members
        assert "ENRICHED" in members


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_base_is_exception(self) -> None:
        assert issubclass(MemoryLayerError, Exception)

    def test_storage_error_is_memory_layer_error(self) -> None:
        assert issubclass(StorageError, MemoryLayerError)

    def test_not_found_errors(self) -> None:
        assert issubclass(MemoryNotFoundError, MemoryLayerError)
        assert issubclass(FactNotFoundError, MemoryLayerError)

    def test_policy_error(self) -> None:
        assert issubclass(PolicyError, MemoryLayerError)

    def test_tenant_isolation_violation(self) -> None:
        assert issubclass(TenantIsolationViolation, MemoryLayerError)

    def test_idempotency_conflict(self) -> None:
        assert issubclass(IdempotencyConflictError, MemoryLayerError)

    def test_schema_version_error(self) -> None:
        assert issubclass(SchemaVersionError, MemoryLayerError)

    def test_extraction_error(self) -> None:
        assert issubclass(ExtractionError, MemoryLayerError)

    def test_capability_not_available(self) -> None:
        assert issubclass(CapabilityNotAvailableError, MemoryLayerError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(MemoryLayerError):
            raise StorageError("disk full")

    def test_raise_tenant_isolation(self) -> None:
        with pytest.raises(TenantIsolationViolation):
            raise TenantIsolationViolation("cross-tenant access")
