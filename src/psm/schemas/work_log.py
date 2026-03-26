from __future__ import annotations
"""Work log schemas — tracking ongoing agent activity.

Each deployed agent accumulates a work log over its lifetime.
Each entry represents one invocation: what triggered it, what
the agent did, and what it produced.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from psm.schemas.agent import SkillType
from psm.schemas.deployment import TriggerType


class WorkLogEntry(BaseModel):
    """A single invocation record for a deployed agent."""

    entry_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)

    # What triggered this work
    trigger_type: TriggerType
    trigger_detail: str = Field(min_length=1)

    # What was done
    skill_type: SkillType
    hypothesis_id: str = Field(min_length=1)
    action_summary: str = Field(min_length=1)

    # Output reference
    output_id: str | None = None  # References stored SkillOutput

    # Timing
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    status: str = "running"  # running | completed | failed | skipped
    error_message: str | None = None

    # Context
    invocation_number: int = Field(ge=1, default=1)
    run_id: str | None = None  # Pipeline run that triggered this, if any


class WorkLog(BaseModel):
    """Aggregated work log for a deployed agent."""

    agent_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    entries: list[WorkLogEntry] = Field(default_factory=list)
    total_invocations: int = Field(ge=0, default=0)
    last_invoked_at: datetime | None = None
    last_output_at: datetime | None = None
