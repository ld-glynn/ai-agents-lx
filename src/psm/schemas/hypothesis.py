from __future__ import annotations
"""Hypothesis schemas — output of the Hypothesis Generator.

Each hypothesis is a testable "If we X, then Y, because Z" statement
linked to a pattern.
"""

from enum import Enum

from pydantic import BaseModel, Field


class EffortEstimate(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OutcomeStatus(str, Enum):
    UNTESTED = "untested"
    TESTING = "testing"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"


class Hypothesis(BaseModel):
    """A testable solution hypothesis linked to a pattern.

    Hard failures:
    - Missing statement or pattern_id → SCHEMA_INVALID
    - Empty test_criteria → MISSING_REQUIRED_FIELD (untestable hypothesis is useless)

    Soft failures:
    - confidence > 0.8 without assumptions → OVERCONFIDENT
    - effort_estimate is HIGH but no assumptions listed → ASSUMPTIONS_MISSING
    """

    model_config = {"strict": True}

    hypothesis_id: str = Field(min_length=1)
    pattern_id: str = Field(min_length=1)
    statement: str = Field(
        min_length=20,
        description="Format: 'If we [action], then [outcome], because [reasoning]'",
    )
    assumptions: list[str] = Field(default_factory=list)
    expected_outcome: str = Field(min_length=10)
    effort_estimate: EffortEstimate
    confidence: float = Field(ge=0.0, le=1.0)
    test_criteria: list[str] = Field(min_length=1)  # must be testable
    risks: list[str] = Field(default_factory=list)
    outcome_status: OutcomeStatus = OutcomeStatus.UNTESTED
