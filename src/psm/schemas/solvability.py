"""Solvability schemas — output of the Solvability Evaluator.

Sits between Pattern Analyzer (input: Pattern) and Hypothesis Generator.
Decides which patterns are worth generating hypotheses for, based on
the system's declared capabilities and historical outcomes.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SolvabilityStatus(str, Enum):
    PASS = "pass"    # proceed to hypothesis generation
    FLAG = "flag"    # hold for human review
    DROP = "drop"    # exclude from further pipeline stages


class SolvabilityResult(BaseModel):
    """Evaluation result for a single pattern."""

    pattern_id: str = Field(min_length=1)
    status: SolvabilityStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=5)
    capability_match: bool = True   # is this within the capability inventory
    signal_strength: float = Field(ge=0.0, le=1.0)  # how much supporting evidence
    actionability: float = Field(ge=0.0, le=1.0)     # actionable vs structural/political
    evaluated_at: datetime = Field(default_factory=datetime.now)


class SolvabilityReport(BaseModel):
    """Full output of the solvability evaluation stage."""

    results: list[SolvabilityResult] = Field(min_length=0)
    total_patterns: int = Field(ge=0)
    passed: int = Field(ge=0)
    flagged: int = Field(ge=0)
    dropped: int = Field(ge=0)
    evaluated_at: datetime = Field(default_factory=datetime.now)


# --- Capability Inventory ---

class SkillCapability(BaseModel):
    """A skill type the system can produce."""
    skill_type: str = Field(min_length=1)
    description: str = Field(min_length=5)
    output_formats: list[str] = Field(min_length=1)


class AgentCapability(BaseModel):
    """An agent archetype the system can create."""
    agent_type: str = Field(min_length=1)
    description: str = Field(min_length=5)
    domains: list[str] = Field(min_length=1)
    skills: list[str] = Field(min_length=1)


class CapabilityInventory(BaseModel):
    """What the system can do — loaded fresh on every evaluator run."""

    agent_types: list[AgentCapability] = Field(min_length=1)
    skill_types: list[SkillCapability] = Field(min_length=1)
    min_problems_per_pattern: int = Field(default=2, ge=1)
    version: str = "1.0"
    updated_at: datetime = Field(default_factory=datetime.now)


# --- Outcome Log ---

class OutcomeEntry(BaseModel):
    """A single outcome record for a tested hypothesis."""

    pattern_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    solvability_score_at_time: float = Field(ge=0.0, le=1.0)
    outcome: str  # "validated" | "invalidated"
    domain: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)
