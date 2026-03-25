"""Tests for the Solvability Evaluator.

Tests that:
1. Dropped patterns don't appear in hypothesis_gen input
2. The capability inventory loads correctly
3. Heuristic evaluator produces valid results for each pattern
4. Patterns below min_problems threshold get dropped
5. Patterns outside capability space get dropped
"""

import json
import tempfile
from pathlib import Path

import pytest

from psm.schemas.pattern import Pattern
from psm.schemas.problem import Domain
from psm.schemas.solvability import (
    CapabilityInventory,
    SolvabilityReport,
    SolvabilityStatus,
    OutcomeEntry,
)
from psm.agents.solvability_evaluator import (
    run_solvability_evaluator,
    load_capability_inventory,
    _evaluate_heuristic,
)


# --- Fixtures ---

@pytest.fixture
def sample_patterns():
    """Two patterns: one clearly solvable, one marginal."""
    return [
        Pattern(
            pattern_id="PAT-001",
            name="Knowledge silos",
            description="Critical systems only documented in people's heads",
            problem_ids=["P-001", "P-002", "P-003"],
            domains_affected=[Domain.KNOWLEDGE],
            frequency=3,
            root_cause_hypothesis=None,
            confidence=0.8,
        ),
        Pattern(
            pattern_id="PAT-002",
            name="Leadership alignment",
            description="Executives disagree on strategic direction, causing conflicting priorities",
            problem_ids=["P-004", "P-005"],
            domains_affected=[Domain.STRATEGY],
            frequency=2,
            root_cause_hypothesis=None,
            confidence=0.5,
        ),
    ]


@pytest.fixture
def sample_inventory():
    return CapabilityInventory(
        agent_types=[{
            "agent_type": "domain_specialist",
            "description": "Expert in a domain",
            "domains": ["knowledge", "process", "tooling", "infrastructure"],
            "skills": ["recommend", "action_plan", "process_doc"],
        }],
        skill_types=[{
            "skill_type": "recommend",
            "description": "Produce a recommendation",
            "output_formats": ["structured_prose"],
        }],
        min_problems_per_pattern=2,
    )


@pytest.fixture
def sample_inventory_narrow():
    """Inventory that only covers infrastructure."""
    return CapabilityInventory(
        agent_types=[{
            "agent_type": "infra_specialist",
            "description": "Infrastructure expert",
            "domains": ["infrastructure"],
            "skills": ["action_plan"],
        }],
        skill_types=[{
            "skill_type": "action_plan",
            "description": "Create an action plan",
            "output_formats": ["structured_prose"],
        }],
        min_problems_per_pattern=2,
    )


# --- Tests ---

class TestHeuristicEvaluator:
    def test_every_pattern_gets_a_result(self, sample_patterns, sample_inventory):
        results = _evaluate_heuristic(sample_patterns, sample_inventory, [])
        result_ids = {r.pattern_id for r in results}
        pattern_ids = {p.pattern_id for p in sample_patterns}
        assert result_ids == pattern_ids

    def test_knowledge_pattern_passes(self, sample_patterns, sample_inventory):
        """Knowledge domain is covered by the inventory, so PAT-001 should pass."""
        results = _evaluate_heuristic(sample_patterns, sample_inventory, [])
        pat001 = next(r for r in results if r.pattern_id == "PAT-001")
        assert pat001.status == SolvabilityStatus.PASS
        assert pat001.capability_match is True

    def test_strategy_pattern_flagged_or_dropped(self, sample_patterns, sample_inventory):
        """Strategy domain has low actionability and may not be in capability space."""
        results = _evaluate_heuristic(sample_patterns, sample_inventory, [])
        pat002 = next(r for r in results if r.pattern_id == "PAT-002")
        # Strategy is not in the inventory domains, so should be dropped
        assert pat002.status in (SolvabilityStatus.FLAG, SolvabilityStatus.DROP)

    def test_outside_capability_space_dropped(self, sample_patterns, sample_inventory_narrow):
        """Patterns whose domains aren't in the inventory get dropped."""
        results = _evaluate_heuristic(sample_patterns, sample_inventory_narrow, [])
        # Neither knowledge nor strategy is in the narrow (infrastructure-only) inventory
        for r in results:
            assert r.status == SolvabilityStatus.DROP
            assert r.capability_match is False

    def test_below_min_problems_dropped(self, sample_inventory):
        """A pattern with fewer problems than min_problems_per_pattern gets dropped."""
        inventory = sample_inventory
        inventory_data = inventory.model_dump()
        inventory_data["min_problems_per_pattern"] = 5  # set high threshold
        strict_inventory = CapabilityInventory.model_validate(inventory_data)

        patterns = [Pattern(
            pattern_id="PAT-SMALL",
            name="Small pattern",
            description="Only two problems supporting this pattern",
            problem_ids=["P-001", "P-002"],
            domains_affected=[Domain.KNOWLEDGE],
            frequency=2,
            confidence=0.7,
        )]
        results = _evaluate_heuristic(patterns, strict_inventory, [])
        assert results[0].status == SolvabilityStatus.DROP
        assert "Insufficient signal" in results[0].reason

    def test_confidence_and_scores_in_range(self, sample_patterns, sample_inventory):
        results = _evaluate_heuristic(sample_patterns, sample_inventory, [])
        for r in results:
            assert 0.0 <= r.confidence <= 1.0
            assert 0.0 <= r.signal_strength <= 1.0
            assert 0.0 <= r.actionability <= 1.0


class TestSolvabilityReport:
    def test_run_produces_valid_report(self, sample_patterns):
        """The full run function produces a valid SolvabilityReport."""
        report = run_solvability_evaluator(sample_patterns)
        assert isinstance(report, SolvabilityReport)
        assert report.total_patterns == len(sample_patterns)
        assert report.passed + report.flagged + report.dropped == report.total_patterns

    def test_empty_patterns_returns_empty_report(self):
        report = run_solvability_evaluator([])
        assert report.total_patterns == 0
        assert report.results == []


class TestDroppedPatternsExcluded:
    def test_dropped_patterns_filtered_before_hypothesis_gen(self, sample_patterns, sample_inventory_narrow):
        """Simulate what the orchestrator does: only pass patterns proceed."""
        results = _evaluate_heuristic(sample_patterns, sample_inventory_narrow, [])
        passed_ids = {r.pattern_id for r in results if r.status == SolvabilityStatus.PASS}
        filtered = [p for p in sample_patterns if p.pattern_id in passed_ids]
        # With narrow inventory (infra-only), no knowledge/strategy patterns should pass
        assert len(filtered) == 0

    def test_passed_patterns_preserved(self, sample_patterns, sample_inventory):
        """Pass patterns should survive the filter."""
        results = _evaluate_heuristic(sample_patterns, sample_inventory, [])
        passed_ids = {r.pattern_id for r in results if r.status == SolvabilityStatus.PASS}
        filtered = [p for p in sample_patterns if p.pattern_id in passed_ids]
        # PAT-001 (knowledge) should pass with the full inventory
        assert any(p.pattern_id == "PAT-001" for p in filtered)


class TestCapabilityInventory:
    def test_inventory_loads_from_file(self):
        """The starter capability_inventory.json file should parse correctly."""
        inventory = load_capability_inventory()
        assert isinstance(inventory, CapabilityInventory)
        assert len(inventory.agent_types) >= 1
        assert len(inventory.skill_types) >= 1
        assert inventory.min_problems_per_pattern >= 1

    def test_inventory_schema_validation(self):
        """Invalid inventory data should raise validation errors."""
        with pytest.raises(Exception):
            CapabilityInventory.model_validate({
                "agent_types": [],  # min_length=1 violated
                "skill_types": [{"skill_type": "x", "description": "test", "output_formats": ["prose"]}],
            })


class TestOutcomeEntry:
    def test_outcome_entry_creation(self):
        entry = OutcomeEntry(
            pattern_id="PAT-001",
            hypothesis_id="HYP-001",
            solvability_score_at_time=0.75,
            outcome="validated",
            domain="knowledge",
        )
        assert entry.outcome == "validated"
        assert entry.solvability_score_at_time == 0.75
