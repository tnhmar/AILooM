"""Primitive domain type aliases and factory functions used throughout memory-layer."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import NewType

# ---------------------------------------------------------------------------
# Typed IDs — NewType over str for JSON serialisation simplicity
# ---------------------------------------------------------------------------

MemoryId = NewType("MemoryId", str)
TenantId = NewType("TenantId", str)
WorkspaceId = NewType("WorkspaceId", str)
PrincipalId = NewType("PrincipalId", str)
SessionId = NewType("SessionId", str)
RunId = NewType("RunId", str)
FactId = NewType("FactId", str)
EntityId = NewType("EntityId", str)
TraceId = NewType("TraceId", str)
AuditId = NewType("AuditId", str)
PolicyId = NewType("PolicyId", str)
ScheduleId = NewType("ScheduleId", str)
JobId = NewType("JobId", str)


# ---------------------------------------------------------------------------
# Factory functions — each wraps str(uuid4())
# ---------------------------------------------------------------------------

def new_memory_id() -> MemoryId:
    """Return a new unique MemoryId."""
    return MemoryId(str(uuid.uuid4()))


def new_tenant_id() -> TenantId:
    """Return a new unique TenantId."""
    return TenantId(str(uuid.uuid4()))


def new_fact_id() -> FactId:
    """Return a new unique FactId."""
    return FactId(str(uuid.uuid4()))


def new_trace_id() -> TraceId:
    """Return a new unique TraceId."""
    return TraceId(str(uuid.uuid4()))


def new_audit_id() -> AuditId:
    """Return a new unique AuditId."""
    return AuditId(str(uuid.uuid4()))


def new_policy_id() -> PolicyId:
    """Return a new unique PolicyId."""
    return PolicyId(str(uuid.uuid4()))


def new_schedule_id() -> ScheduleId:
    """Return a new unique ScheduleId."""
    return ScheduleId(str(uuid.uuid4()))


def new_job_id() -> JobId:
    """Return a new unique JobId."""
    return JobId(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Enums — all StrEnum for JSON round-trip (Python 3.11+)
# ---------------------------------------------------------------------------

class LifecycleState(StrEnum):
    """Lifecycle states a MemoryRecord can occupy."""

    ACTIVE = "ACTIVE"
    PROPOSED = "PROPOSED"
    CONSOLIDATED = "CONSOLIDATED"
    DECAYED = "DECAYED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class PipelineStatus(StrEnum):
    """Enrichment pipeline processing status for a MemoryRecord."""

    PENDING = "PENDING"
    ENRICHED = "ENRICHED"
    PARTIAL_ENRICHMENT_FAILED = "PARTIAL_ENRICHMENT_FAILED"
    ENRICHMENT_SKIPPED = "ENRICHMENT_SKIPPED"


class MemorySector(StrEnum):
    """Cognitive sector a memory record is classified into."""

    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROCEDURAL = "PROCEDURAL"
    IDENTITY = "IDENTITY"
    RELATIONAL = "RELATIONAL"
    REFLECTIVE = "REFLECTIVE"


class PayloadType(StrEnum):
    """Semantic type of the raw payload stored in a MemoryRecord."""

    CONVERSATION_TURN = "CONVERSATION_TURN"
    DOCUMENT = "DOCUMENT"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    EVENT = "EVENT"
    STRUCTURED = "STRUCTURED"


class PrincipalType(StrEnum):
    """Type of the principal (actor) interacting with the memory layer."""

    USER = "USER"
    AGENT = "AGENT"


class AuditOperation(StrEnum):
    """Operations recorded in the audit log."""

    WRITE = "WRITE"
    SEARCH = "SEARCH"
    RECALL = "RECALL"
    CONSOLIDATE = "CONSOLIDATE"
    DECAY = "DECAY"
    ARCHIVE = "ARCHIVE"
    DELETE = "DELETE"
    MIGRATION = "MIGRATION"


class AuditOutcome(StrEnum):
    """Outcome of an audited operation."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
