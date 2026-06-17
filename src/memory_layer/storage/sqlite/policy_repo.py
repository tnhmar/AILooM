"""SQLite adapter for TenantPolicyRepositoryPort.

Design notes
------------
- Each tenant's policy aggregate is serialised as a single JSON blob via
  ``dataclasses.asdict`` and stored in a ``tenant_policies`` table keyed by
  ``tenant_id``.
- ``save`` uses ``INSERT OR REPLACE`` so upserts are idempotent.
- ``get`` returns a freshly constructed ``TenantPolicies()`` with all
  defaults when no row exists for the tenant (open-world default).
- Deserialisation is enum-aware: string values for known ``StrEnum`` fields
  (``ConsolidationTrigger``, ``ConflictResolutionMode``, ``MemorySector``)
  are coerced back to their enum types before constructing the dataclass.
- ``policy_id`` fields embedded in sub-policies are preserved as opaque
  strings through the round-trip since ``PolicyId`` is a ``NewType(str)``.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import aiosqlite

from memory_layer.domain.exceptions import StorageError
from memory_layer.domain.policies import (
    ConflictResolutionMode,
    ConflictResolutionPolicy,
    ConsolidationPolicy,
    ConsolidationTrigger,
    RetentionPolicy,
    SearchWeightsPolicy,
    TenantPolicies,
)
from memory_layer.domain.types import MemorySector, PolicyId, TenantId


class SqliteTenantPolicyRepository:
    """SQLite-backed repository for per-tenant :class:`TenantPolicies`.

    Parameters
    ----------
    db_path:
        Path to the SQLite file, or ``":memory:"`` for an in-process database.
        The V1 schema must already be applied before instantiation.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _to_json(self, policies: TenantPolicies) -> str:
        """Serialise *policies* to a JSON string via ``dataclasses.asdict``.

        ``StrEnum`` members serialise naturally as their string values.
        ``MemorySector`` list members likewise.
        """
        return json.dumps(dataclasses.asdict(policies))

    def _from_json(self, raw: str) -> TenantPolicies:
        """Deserialise *raw* JSON string back to a :class:`TenantPolicies`.

        Enum-aware: string values in known enum fields are coerced to the
        correct ``StrEnum`` type so callers receive fully typed objects.
        """
        data: dict[str, Any] = json.loads(raw)
        return TenantPolicies(
            consolidation=self._load_consolidation(data.get("consolidation", {})),
            retention=self._load_retention(data.get("retention", {})),
            conflict_resolution=self._load_conflict_resolution(
                data.get("conflict_resolution", {})
            ),
            search_weights=self._load_search_weights(data.get("search_weights", {})),
        )

    @staticmethod
    def _load_consolidation(d: dict[str, Any]) -> ConsolidationPolicy:
        return ConsolidationPolicy(
            policy_id=PolicyId(d["policy_id"]),
            trigger=ConsolidationTrigger(d["trigger"]),
            threshold_record_count=int(d["threshold_record_count"]),
            max_items_per_run=int(d["max_items_per_run"]),
            sectors=[MemorySector(s) for s in d.get("sectors", [])],
            enabled=bool(d["enabled"]),
        )

    @staticmethod
    def _load_retention(d: dict[str, Any]) -> RetentionPolicy:
        return RetentionPolicy(
            policy_id=PolicyId(d["policy_id"]),
            decay_after_days=d.get("decay_after_days"),
            archive_after_days=d.get("archive_after_days"),
            delete_after_days=d.get("delete_after_days"),
            sector_decay_overrides=d.get("sector_decay_overrides", {}),
            enabled=bool(d["enabled"]),
        )

    @staticmethod
    def _load_conflict_resolution(d: dict[str, Any]) -> ConflictResolutionPolicy:
        return ConflictResolutionPolicy(
            policy_id=PolicyId(d["policy_id"]),
            mode=ConflictResolutionMode(d["mode"]),
            low_confidence_threshold=float(d["low_confidence_threshold"]),
            enabled=bool(d["enabled"]),
        )

    @staticmethod
    def _load_search_weights(d: dict[str, Any]) -> SearchWeightsPolicy:
        return SearchWeightsPolicy(
            policy_id=PolicyId(d["policy_id"]),
            semantic_weight=float(d["semantic_weight"]),
            keyword_weight=float(d["keyword_weight"]),
            entity_weight=float(d["entity_weight"]),
            recency_weight=float(d["recency_weight"]),
            salience_weight=float(d["salience_weight"]),
            enabled=bool(d["enabled"]),
        )

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    async def get(self, tenant_id: TenantId) -> TenantPolicies:
        """Return the policies for *tenant_id*.

        Returns a default :class:`TenantPolicies` instance (all defaults) when
        no row exists for the tenant.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        sql = "SELECT policies_json FROM tenant_policies WHERE tenant_id = ?"
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(sql, (str(tenant_id),)) as cursor:
                    row = await cursor.fetchone()
        except Exception as exc:
            raise StorageError(f"get failed for tenant_id={tenant_id}: {exc}") from exc

        if row is None:
            return TenantPolicies()
        return self._from_json(row["policies_json"])

    async def save(self, tenant_id: TenantId, policies: TenantPolicies) -> None:
        """Persist *policies* for *tenant_id*.  Idempotent (upsert).

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        sql = """
            INSERT OR REPLACE INTO tenant_policies (tenant_id, policies_json)
            VALUES (?, ?)
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(sql, (str(tenant_id), self._to_json(policies)))
                await db.commit()
        except Exception as exc:
            raise StorageError(f"save failed for tenant_id={tenant_id}: {exc}") from exc
