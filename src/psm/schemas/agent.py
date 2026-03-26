from __future__ import annotations
"""Agent schemas — the three-tier agent model.

Tier 1: Engine Agents (Cataloger, Pattern Analyzer, etc.) — defined in code, not data.
Tier 2: Agent New Hires — created by the Hiring Manager per problem cluster.
Tier 3: Skills — repeatable capabilities each New Hire can use.

This file defines Tier 2 and Tier 3 as data contracts.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SkillType(str, Enum):
    """Tier 3: Skills an Agent New Hire can use."""
    RECOMMEND = "recommend"
    ACTION_PLAN = "action_plan"
    PROCESS_DOC = "process_doc"
    INVESTIGATE = "investigate"


class AgentSkill(BaseModel):
    """A repeatable skill assigned to a New Hire."""

    model_config = {"strict": True}

    skill_type: SkillType
    hypothesis_id: str = Field(min_length=1)
    priority: int = Field(ge=1, le=5)
    status: str = "pending"  # pending | in_progress | complete
    last_executed_at: datetime | None = None
    execution_count: int = 0


class AgentNewHire(BaseModel):
    """Tier 2: A specialized agent created to own a specific problem cluster.

    Created by the Hiring Manager based on pattern analysis.
    Each New Hire gets:
    - A name and persona tailored to their problem domain
    - Context about the pattern they own
    - A set of skills (Tier 3) they can use repeatedly
    - Assignment to a team role for collaboration
    - A lifecycle state tracking their progression from candidate to deployed worker
    """

    model_config = {"strict": True}

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    persona: str = Field(min_length=10)
    pattern_id: str = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=1)
    skills: list[AgentSkill] = Field(min_length=1)
    assigned_to_role: str | None = None
    model: str = "claude-sonnet-4-20250514"
    created_at: datetime = Field(default_factory=datetime.now)

    # Lifecycle
    lifecycle_state: str = "created"  # matches AgentLifecycleState values
    deployment_spec_id: str | None = None


class SkillOutput(BaseModel):
    """The deliverable produced when a New Hire uses a skill.

    Each invocation of a deployed agent produces SkillOutput entries,
    tracked by invocation_number and linked to the work log.
    """

    model_config = {"strict": True}

    agent_id: str = Field(min_length=1)
    skill_type: SkillType
    hypothesis_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=10)
    next_steps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    reviewed: bool = False

    # Lifecycle linkage
    work_log_entry_id: str | None = None
    invocation_number: int = 1
