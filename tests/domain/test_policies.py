"""Tests for domain policy configuration objects."""

from __future__ import annotations

import math

from memory_layer.domain.policies import (
    ConflictResolutionMode,
    ConflictResolutionPolicy,
    ConsolidationPolicy,
    ConsolidationTrigger,
    RetentionPolicy,
    SearchWeightsPolicy,
    TenantPolicies,
)
from memory_layer.domain.types import MemorySector


class TestConsolidationPolicy:
    def test_default_trigger_is_session_end(self) -> None:
        assert ConsolidationPolicy().trigger == ConsolidationTrigger.SESSION_END

    def test_default_threshold_record_count(self) -> None:
        assert ConsolidationPolicy().threshold_record_count == 500

    def test_default_sectors_contains_all_six(self) -> None:
        sectors = ConsolidationPolicy().sectors
        assert set(sectors) == set(MemorySector)
        assert len(sectors) == 6

    def test_sectors_not_shared_across_instances(self) -> None:
        p1 = ConsolidationPolicy()
        p2 = ConsolidationPolicy()
        assert p1.sectors is not p2.sectors


class TestRetentionPolicy:
    def test_default_decay_after_days(self) -> None:
        assert RetentionPolicy().decay_after_days == 90

    def test_default_delete_after_days_is_none(self) -> None:
        assert RetentionPolicy().delete_after_days is None


class TestConflictResolutionPolicy:
    def test_default_mode_is_auto_close(self) -> None:
        assert ConflictResolutionPolicy().mode == ConflictResolutionMode.AUTO_CLOSE

    def test_default_low_confidence_threshold(self) -> None:
        assert ConflictResolutionPolicy().low_confidence_threshold == 0.6

    def test_low_confidence_fact_below_threshold(self) -> None:
        policy = ConflictResolutionPolicy()
        confidence = 0.3
        assert confidence < policy.low_confidence_threshold


class TestSearchWeightsPolicy:
    def test_total_weight_approximates_one(self) -> None:
        assert math.isclose(SearchWeightsPolicy().total_weight(), 1.0, rel_tol=1e-9)


class TestTenantPolicies:
    def test_constructs_with_all_sub_policies(self) -> None:
        tp = TenantPolicies()
        assert isinstance(tp.consolidation, ConsolidationPolicy)
        assert isinstance(tp.retention, RetentionPolicy)
        assert isinstance(tp.conflict_resolution, ConflictResolutionPolicy)
        assert isinstance(tp.search_weights, SearchWeightsPolicy)

    def test_independent_consolidation_objects(self) -> None:
        tp1 = TenantPolicies()
        tp2 = TenantPolicies()
        assert tp1.consolidation is not tp2.consolidation


class TestEnumRoundTrip:
    def test_conflict_resolution_mode_round_trip(self) -> None:
        assert ConflictResolutionMode("AUTO_CLOSE") == ConflictResolutionMode.AUTO_CLOSE

    def test_consolidation_trigger_round_trip(self) -> None:
        assert ConsolidationTrigger("ON_DEMAND") == ConsolidationTrigger.ON_DEMAND
