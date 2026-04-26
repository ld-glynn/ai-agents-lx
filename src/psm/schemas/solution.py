"""Solution schemas — output of the Solver Router and Solver agents.

SolutionMapping: connects a hypothesis to a solver type + team role.
SolverOutput: the actual deliverable produced by a solver agent.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SolverType(str, Enum):
    RECOMMENDATION = "recommendation"
    ACTION_PLAN = "action_plan"
    PROCESS_DOC = "process_doc"
    INVESTIGATION = "investigation"  # needs more info before acting


class SolutionStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class SolutionMapping(BaseModel):
    """Maps a hypothesis to a solver type and optionally a team role."""

    mapping_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    solver_type: SolverType
    assigned_to_role: str | None = None
    rationale: str = Field(min_length=10)
    priority: int = Field(ge=1, le=5)  # 1 = highest
    status: SolutionStatus = SolutionStatus.PENDING


class SolverOutput(BaseModel):
    """The deliverable produced by a solver agent."""

    mapping_id: str = Field(min_length=1)
    solver_type: SolverType
    title: str = Field(min_length=1)
    content: str = Field(min_length=10)
    next_steps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    reviewed: bool = False
