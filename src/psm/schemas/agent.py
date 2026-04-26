"""Agent schemas — the three-tier agent model.

Tier 1: Engine Agents (Cataloger, Pattern Analyzer, etc.) — defined in code, not data.
Tier 2: Agent New Hires — created by the Hiring Manager per problem cluster.
Tier 3: Skills — repeatable capabilities each New Hire can use.

This file defines Tier 2 and Tier 3 as data contracts.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SkillType(str, Enum):
    """Tier 3: Skills an Agent New Hire can use."""
    RECOMMEND = "recommend"
    ACTION_PLAN = "action_plan"
    PROCESS_DOC = "process_doc"
    INVESTIGATE = "investigate"


class AgentSkill(BaseModel):
    """A repeatable skill assigned to a New Hire."""

    skill_type: SkillType
    hypothesis_id: str = Field(min_length=1)
    priority: int = Field(ge=1, le=5)
    status: str = "pending"  # pending | in_progress | complete
    last_executed_at: datetime | None = None
    execution_count: int = 0


class CaseFieldType(str, Enum):
    """Types the Hiring Manager may declare on a case field.

    Intentionally a small closed set — we want the Hiring Manager to commit
    to a concrete type per field, not hand us back 'it depends'. REFERENCE
    means the field holds an identifier pointing into an upstream source
    (e.g. a Zendesk ticket ID, a Salesforce account ID).
    """

    STRING = "string"       # short free-form, e.g. a label
    TEXT = "text"           # long-form content, e.g. a drafted response
    INTEGER = "integer"
    NUMBER = "number"       # float
    BOOLEAN = "boolean"
    ENUM = "enum"           # requires enum_values
    DATE = "date"           # ISO 8601 date or datetime
    REFERENCE = "reference" # identifier pointing to an upstream record
    LIST = "list"           # list of strings or references


class CaseField(BaseModel):
    """One typed field on a case the agent produces.

    These are the DOMAIN fields the Hiring Manager specifies. Runtime
    infrastructure fields (case_id, created_at, status, audit_log) are
    added by the runtime layer and must NOT appear in this list.
    """

    name: str = Field(min_length=1)
    field_type: CaseFieldType
    description: str = Field(min_length=5)
    required: bool = True
    enum_values: list[str] | None = None  # must be non-empty when field_type == ENUM
    # Concrete example to anchor the field. Accepts any JSON primitive so an
    # integer field's example can be 180, not the string "180". The example
    # is descriptive only — not validated against field_type at parse time.
    example: Any = None


class CaseSchema(BaseModel):
    """The structured contract for a case the agent produces.

    Replaces the previous free-prose case_unit_description. Its purpose is
    to be enforceable: at runtime, a case's domain fields are validated
    against this schema before being persisted. The runtime is not built
    yet, but the schema has to be right first.
    """

    summary: str = Field(min_length=10)  # one-sentence description of what a case represents
    fields: list[CaseField] = Field(min_length=1)

    # Traceability rules: each entry is an assertion that must hold for
    # every case the agent persists. Example: "Every case must reference
    # at least one Zendesk ticket by ID." "Drafted responses must cite a
    # specific section of the pricing documentation."
    evidence_requirements: list[str] = Field(min_length=1)


class JobFunction(BaseModel):
    """Tier 3 (ongoing): What a deployed agent-employee does on an ongoing basis.

    Distinct from AgentSkill (recommend/action_plan/process_doc/investigate)
    which describes one-shot hire-time document outputs. A JobFunction
    describes recurring work the agent does AFTER being hired — the actual
    job, not the pitch for the job.

    case_schema is the structured contract for what a single case looks
    like. Internal pipeline stages and lifecycle state machines are
    deliberately deferred — they're runtime concerns and we should see
    this resolution work first.
    """

    function_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    mission: str = Field(min_length=10)  # one-sentence purpose
    pattern_id: str = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=1)

    # The key distinction from AgentSkill: ongoing, not one-shot
    triggers: list[str] = Field(min_length=1)  # when/how the agent wakes up
    upstream_sources: list[str] = Field(default_factory=list)  # data the agent reads from

    # The unit of work the agent produces and manages
    case_unit_name: str = Field(min_length=1)  # e.g. "ticket triage decision", "daily digest"
    case_schema: CaseSchema

    # Load-bearing per elliot: prevents mission creep
    out_of_scope: list[str] = Field(min_length=1)

    # Governance
    decision_rights: list[str] = Field(min_length=1)  # what the agent can decide autonomously
    human_approval_required: list[str] = Field(default_factory=list)  # what needs human in the loop

    # How we measure it in production
    performance_metrics: list[str] = Field(min_length=1)


class AgentNewHire(BaseModel):
    """Tier 2: A specialized agent created to own a specific problem cluster.

    Created by the Hiring Manager based on pattern analysis.
    Each New Hire gets:
    - A name and persona tailored to their problem domain
    - Context about the pattern they own
    - A set of hire-time skills (AgentSkill) — the "pitch deck" for the role
    - A set of ongoing job functions (JobFunction) — the actual recurring work
    - Assignment to a team role for collaboration
    - A lifecycle state tracking their progression from candidate to deployed worker
    """

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    persona: str = Field(min_length=10)
    pattern_id: str = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=1)
    skills: list[AgentSkill] = Field(min_length=1)
    # Ongoing job functions — optional for backward compatibility with existing
    # AgentNewHire records that pre-date this schema. New hires produced by the
    # updated Hiring Manager should always have at least one.
    job_functions: list[JobFunction] = Field(default_factory=list)
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
