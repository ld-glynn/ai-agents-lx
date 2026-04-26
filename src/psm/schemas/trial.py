"""Trial schemas — time-bounded hypothesis validation for deployed agents.

A trial wraps an agent deployment with defined success criteria, periodic
check-ins, and a final verdict that feeds back into the solvability evaluator.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TrialStatus(str, Enum):
    SETUP = "setup"
    ACTIVE = "active"
    EVALUATING = "evaluating"
    COMPLETED = "completed"


class TrialVerdict(str, Enum):
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    EXTENDED = "extended"
    INCONCLUSIVE = "inconclusive"


class CheckInProgress(str, Enum):
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    OFF_TRACK = "off_track"


class CheckIn(BaseModel):
    """A periodic check-in during a trial."""

    check_in_id: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=datetime.now)
    note: str = Field(min_length=1)
    progress_indicator: CheckInProgress = CheckInProgress.ON_TRACK


class Trial(BaseModel):
    """A time-bounded evaluation of whether an agent validates its hypothesis."""

    trial_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    pattern_id: str = Field(min_length=1)

    # Configuration
    duration_days: int = Field(default=30, ge=1)
    success_criteria: list[str] = Field(min_length=1)
    check_in_frequency_days: int = Field(default=7, ge=1)

    # Timeline
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    ends_at: datetime | None = None
    completed_at: datetime | None = None

    # State
    status: TrialStatus = TrialStatus.SETUP
    check_ins: list[CheckIn] = Field(default_factory=list)

    # Verdict
    verdict: TrialVerdict | None = None
    verdict_note: str | None = None
    lessons_learned: list[str] = Field(default_factory=list)
