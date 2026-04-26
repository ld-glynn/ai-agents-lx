"""Brief schemas — AI-generated executive narratives for pipeline entities.

Briefs are cached narrative documents that synthesize an entity's data,
evidence, and context into a presentable format for stakeholders.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Brief(BaseModel):
    """An AI-generated executive brief for a pipeline entity."""

    entity_type: str = Field(min_length=1)  # "problem", "pattern", "hypothesis"
    entity_id: str = Field(min_length=1)
    content: str = Field(min_length=1)  # Markdown narrative
    generated_at: datetime = Field(default_factory=datetime.now)
    model_used: str = "claude-sonnet-4-20250514"
