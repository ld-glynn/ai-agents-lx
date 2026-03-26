from __future__ import annotations
"""Run history schemas — tracking pipeline executions and enabling rollback."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RunRecord(BaseModel):
    """A single pipeline execution record."""

    run_id: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=datetime.now)
    stages_completed: list[str] = Field(default_factory=list)
    stage_reached: str = ""
    status: Literal["running", "success", "partial", "failed"] = "running"
    error_stage: str | None = None
    error_message: str | None = None
    snapshot_path: str = ""
    summary: dict = Field(default_factory=dict)
    config_used: dict = Field(default_factory=dict)
