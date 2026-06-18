"""ExplainRecallService — concrete ExplainRecallUseCase."""

from __future__ import annotations

import logging

from memory_layer.domain.exceptions import MemoryNotFoundError
from memory_layer.domain.records import RecallTrace, TraceStep
from memory_layer.domain.types import TenantId, TraceId
from memory_layer.ports.inbound import ExplainRecallUseCase
from memory_layer.ports.outbound import (
    AuditLogPort,
    MemoryRecordRepositoryPort,
    TraceRepositoryPort,
)

log = logging.getLogger(__name__)


class ExplainRecallService:
    """Implements ExplainRecallUseCase.

    Flow:
    1. Fetch the RecallTrace from trace_repo.
    2. Raise MemoryNotFoundError if absent.
    3. For each TraceStep: attempt to load the associated MemoryRecord.
       Mark step.record_available=False if the record no longer exists.
    4. Return the fully populated RecallTrace.
    """

    def __init__(
        self,
        trace_repo: TraceRepositoryPort,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
    ) -> None:
        self._trace_repo = trace_repo
        self._record_repo = record_repo
        self._audit_log = audit_log

    async def execute(
        self, trace_id: TraceId, tenant_id: TenantId
    ) -> RecallTrace:
        trace = await self._trace_repo.get_by_trace_id(trace_id, tenant_id)

        if trace is None:
            raise MemoryNotFoundError(f"Trace {trace_id} not found")

        for step in trace.steps:
            record = await self._record_repo.get_by_id(
                memory_id=step.memory_id,
                tenant_id=tenant_id,
            )
            if record is None:
                log.debug(
                    "Record %s in trace %s no longer exists — marking unavailable.",
                    step.memory_id,
                    trace_id,
                )
                step.record_available = False
            else:
                step.record_available = True

        return trace
