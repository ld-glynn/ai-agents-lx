"""Problem schemas — the foundation contract.

RawProblem: direct mapping from CSV row (Google Sheet export).
CatalogEntry: normalized by the Cataloger agent, validated strictly.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(str, Enum):
    """Problem domains — extend as your team's landscape grows."""
    PROCESS = "process"
    TOOLING = "tooling"
    COMMUNICATION = "communication"
    KNOWLEDGE = "knowledge"
    INFRASTRUCTURE = "infrastructure"
    PEOPLE = "people"
    STRATEGY = "strategy"
    CUSTOMER = "customer"
    OTHER = "other"


class RawProblem(BaseModel):
    """Direct mapping from a CSV row or Wisdom discovery. Minimal validation."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reported_by: str = Field(min_length=1)
    date_reported: str = Field(min_length=1)
    domain: str | None = None
    tags: str | None = None  # comma-separated from sheet
    # Provenance fields from Wisdom enrichment (optional — absent for CSV imports)
    agent_idea: str | None = None
    synthesis: str | None = None
    evidence: str | None = None
    upstream_sources: list[str] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    source_record_ids: list[str] = Field(default_factory=list)


class CatalogEntry(BaseModel):
    """Normalized by the Cataloger agent. This is the strict contract.

    Hard failures (Elliot-inspired):
    - Missing problem_id, title, or description_normalized → SCHEMA_INVALID
    - Invalid domain or severity enum → FIELD_MISMATCH
    - Empty tags list → MISSING_REQUIRED_FIELD

    Soft failures:
    - description_normalized identical to raw description → NORMALIZATION_SKIPPED
    - reporter_role is None → MISSING_OPTIONAL_FIELD
    """

    problem_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description_normalized: str = Field(min_length=10)
    domain: Domain
    severity: Severity
    tags: list[str] = Field(min_length=1)
    reporter_role: str | None = None
    affected_roles: list[str] = Field(default_factory=list)
    frequency: str | None = None  # "daily", "weekly", "monthly", "rare"
    impact_summary: str | None = None
    cataloged_at: datetime = Field(default_factory=datetime.now)
    # Provenance — carried forward from RawProblem
    source_record_ids: list[str] = Field(default_factory=list)
    agent_idea: str | None = None
    upstream_sources: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def tags_must_be_lowercase_snake(cls, v: list[str]) -> list[str]:
        for tag in v:
            if tag != tag.lower().replace(" ", "_"):
                raise ValueError(f"Tag must be lowercase snake_case: {tag}")
        return v
