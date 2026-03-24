from __future__ import annotations
"""Agent schemas — the three-tier agent model.

Tier 1: Engine Agents (Cataloger, Pattern Analyzer, etc.) — defined in code, not data.
Tier 2: Agent New Hires — created by the Solver Router per problem cluster.
Tier 3: Skills — capabilities each New Hire can use.

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
    # Future skills:
    # FILE_TICKET = "file_ticket"
    # SEND_UPDATE = "send_update"
    # CREATE_DOC = "create_doc"


class AgentSkill(BaseModel):
    """A specific skill assigned to a New Hire, with context for how to use it."""

    model_config = {"strict": True}

    skill_type: SkillType
    hypothesis_id: str = Field(min_length=1)
    priority: int = Field(ge=1, le=5)
    status: str = "pending"  # pending | in_progress | complete


class AgentNewHire(BaseModel):
    """Tier 2: A specialized agent created to address a specific problem cluster.

    Created by the Solver Router based on pattern analysis.
    Each New Hire gets:
    - A name and persona tailored to their problem domain
    - Context about the pattern they're solving
    - A set of skills (Tier 3) they can use
    - Assignment to a team role for collaboration
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


class SkillOutput(BaseModel):
    """The deliverable produced when a New Hire uses a skill."""

    model_config = {"strict": True}

    agent_id: str = Field(min_length=1)
    skill_type: SkillType
    hypothesis_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=10)
    next_steps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    reviewed: bool = False
