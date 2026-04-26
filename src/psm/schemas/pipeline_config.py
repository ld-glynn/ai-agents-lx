"""Pipeline configuration schemas — per-stage settings.

All fields have defaults, so an empty {} produces valid default config.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CatalogerConfig(BaseModel):
    severity_threshold: Literal["low", "medium", "high", "critical"] = "medium"
    domain_filter: list[str] | None = None  # None = all domains
    model: str | None = None  # Override model for this stage (e.g., haiku for cost savings)


class PatternAnalyzerConfig(BaseModel):
    min_cluster_size: int = Field(default=2, ge=2)
    max_patterns: int = Field(default=20, ge=1)
    clustering_strictness: float = Field(default=0.5, ge=0.0, le=1.0)
    model: str | None = None


class SolvabilityConfig(BaseModel):
    min_signal_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    actionability_threshold: float = Field(default=0.4, ge=0.0, le=1.0)


class HypothesisGenConfig(BaseModel):
    max_hypotheses_per_pattern: int = Field(default=3, ge=1, le=10)
    confidence_floor: float = Field(default=0.3, ge=0.0, le=1.0)
    effort_preference: Literal["low", "medium", "high", "any"] = "any"


class HiringManagerConfig(BaseModel):
    max_agents: int = Field(default=10, ge=1)
    preferred_skills: list[str] | None = None


class PipelineConfig(BaseModel):
    """Full pipeline configuration. All sections optional with defaults."""
    cataloger: CatalogerConfig = CatalogerConfig()
    pattern_analyzer: PatternAnalyzerConfig = PatternAnalyzerConfig()
    solvability: SolvabilityConfig = SolvabilityConfig()
    hypothesis_gen: HypothesisGenConfig = HypothesisGenConfig()
    hiring_manager: HiringManagerConfig = HiringManagerConfig()
