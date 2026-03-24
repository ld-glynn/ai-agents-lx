from __future__ import annotations
"""Pattern schemas — output of the Pattern Analyzer.

Patterns cluster related problems. Themes group patterns.
"""

from pydantic import BaseModel, Field

from psm.schemas.problem import Domain


class Pattern(BaseModel):
    """A cluster of related problems with a shared root cause signal."""

    model_config = {"strict": True}

    pattern_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=10)
    problem_ids: list[str] = Field(min_length=2)  # must link ≥2 problems
    domains_affected: list[Domain] = Field(min_length=1)
    frequency: int = Field(ge=2)  # how many problems exhibit this
    root_cause_hypothesis: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ThemeSummary(BaseModel):
    """High-level grouping of patterns. Used by Hypothesis Generator."""

    model_config = {"strict": True}

    theme_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    pattern_ids: list[str] = Field(min_length=1)
    summary: str = Field(min_length=10)
    priority_score: float = Field(ge=0.0, le=10.0)
