"""Policy configuration objects consumed by the engine and policy layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from memory_layer.domain.types import (
    MemorySector,
    PolicyId,
    new_policy_id,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConsolidationTrigger(StrEnum):
    """Event that triggers a consolidation run."""

    SESSION_END = "SESSION_END"
    THRESHOLD = "THRESHOLD"
    SCHEDULE = "SCHEDULE"
    ON_DEMAND = "ON_DEMAND"


class ConflictResolutionMode(StrEnum):
    """Strategy used to resolve contradicting facts."""

    AUTO_CLOSE = "AUTO_CLOSE"
    FLAG_ONLY = "FLAG_ONLY"
    MANUAL = "MANUAL"
    LLM_ARBITRATED = "LLM_ARBITRATED"


# ---------------------------------------------------------------------------
# Policy dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationPolicy:
    """Controls when and how memory records are consolidated."""

    policy_id: PolicyId = field(default_factory=new_policy_id)
    trigger: ConsolidationTrigger = ConsolidationTrigger.SESSION_END
    threshold_record_count: int = 500
    max_items_per_run: int = 200
    sectors: list[MemorySector] = field(default_factory=lambda: list(MemorySector))
    enabled: bool = True


@dataclass
class RetentionPolicy:
    """Controls lifecycle decay, archival, and deletion timelines."""

    policy_id: PolicyId = field(default_factory=new_policy_id)
    decay_after_days: int | None = 90
    archive_after_days: int | None = 365
    delete_after_days: int | None = None
    sector_decay_overrides: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class ConflictResolutionPolicy:
    """Controls how contradicting facts are detected and resolved."""

    policy_id: PolicyId = field(default_factory=new_policy_id)
    mode: ConflictResolutionMode = ConflictResolutionMode.AUTO_CLOSE
    low_confidence_threshold: float = 0.6
    enabled: bool = True


@dataclass
class SearchWeightsPolicy:
    """Configures the relative weight of each retrieval signal in hybrid search."""

    policy_id: PolicyId = field(default_factory=new_policy_id)
    semantic_weight: float = 0.5
    keyword_weight: float = 0.2
    entity_weight: float = 0.15
    recency_weight: float = 0.1
    salience_weight: float = 0.05
    enabled: bool = True

    def total_weight(self) -> float:
        """Return the sum of all retrieval signal weights."""
        return (
            self.semantic_weight
            + self.keyword_weight
            + self.entity_weight
            + self.recency_weight
            + self.salience_weight
        )


@dataclass
class TenantPolicies:
    """Aggregate of all policy objects governing a single tenant."""

    consolidation: ConsolidationPolicy = field(default_factory=ConsolidationPolicy)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    conflict_resolution: ConflictResolutionPolicy = field(
        default_factory=ConflictResolutionPolicy
    )
    search_weights: SearchWeightsPolicy = field(default_factory=SearchWeightsPolicy)
