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
# 1. All factory functions return non-empty strings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "factory",
    [
        new_memory_id,
        new_tenant_id,
        new_fact_id,
        new_trace_id,
        new_audit_id,
        new_policy_id,
        new_schedule_id,
        new_job_id,
    ],
)
def test_factory_returns_nonempty_string(factory):
    result = factory()
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# 2. Two consecutive factory calls produce different values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "factory",
    [
        new_memory_id,
        new_tenant_id,
        new_fact_id,
        new_trace_id,
        new_audit_id,
        new_policy_id,
        new_schedule_id,
        new_job_id,
    ],
)
def test_factory_produces_unique_values(factory):
    assert factory() != factory()


# ---------------------------------------------------------------------------
# 3. LifecycleState str enum round-trip
# ---------------------------------------------------------------------------

def test_lifecycle_state_str_roundtrip():
    assert LifecycleState("ACTIVE") == LifecycleState.ACTIVE


# ---------------------------------------------------------------------------
# 4. PipelineStatus.PENDING is the first enum member
# ---------------------------------------------------------------------------

def test_pipeline_status_pending_is_first():
    members = list(PipelineStatus)
    assert members[0] == PipelineStatus.PENDING


# ---------------------------------------------------------------------------
# 5. TenantIsolationViolation attributes
# ---------------------------------------------------------------------------

def test_tenant_isolation_violation_attributes():
    exc = TenantIsolationViolation("alice", "t-99")
    assert exc.actor == "alice"
    assert exc.requested_tenant_id == "t-99"


# ---------------------------------------------------------------------------
# 6. CapabilityNotAvailableError.capability
# ---------------------------------------------------------------------------

def test_capability_not_available_capability():
    exc = CapabilityNotAvailableError("graph")
    assert exc.capability == "graph"


# ---------------------------------------------------------------------------
# 7. SchemaVersionError attributes
# ---------------------------------------------------------------------------

def test_schema_version_error_attributes():
    exc = SchemaVersionError(1, 2)
    assert exc.expected == 1
    assert exc.found == 2


# ---------------------------------------------------------------------------
# 8. MemoryNotFoundError.memory_id
# ---------------------------------------------------------------------------

def test_memory_not_found_memory_id():
    exc = MemoryNotFoundError("abc")
    assert exc.memory_id == "abc"


# ---------------------------------------------------------------------------
# 9. All exceptions are subclasses of MemoryLayerError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc_class",
    [
        TenantIsolationViolation,
        MemoryNotFoundError,
        FactNotFoundError,
        IdempotencyConflictError,
        CapabilityNotAvailableError,
        ExtractionError,
        StorageError,
        SchemaVersionError,
        PolicyError,
    ],
)
def test_all_exceptions_inherit_base(exc_class):
    assert issubclass(exc_class, MemoryLayerError)


# ---------------------------------------------------------------------------
# 10. All enums support str(member) returning the value string
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "member, expected",
    [
        (LifecycleState.ACTIVE, "ACTIVE"),
        (LifecycleState.CONSOLIDATED, "CONSOLIDATED"),
        (PipelineStatus.PENDING, "PENDING"),
        (PipelineStatus.ENRICHED, "ENRICHED"),
        (MemorySector.EPISODIC, "EPISODIC"),
        (MemorySector.SEMANTIC, "SEMANTIC"),
    ],
)
def test_enum_str_returns_value(member, expected):
    assert str(member) == expected
