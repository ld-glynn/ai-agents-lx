"""Deployment schemas — the persistent agent lifecycle.

A DeploymentSpec is the portable blueprint for deploying an agent.
It describes what the agent does, what triggers its behavior, and
what outputs it produces — independent of where it runs.

The spec is deployment-target agnostic. A DeploymentAdapter translates
the spec into a specific runtime (local, AWS Lambda, Agent SDK, etc.).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AgentLifecycleState(str, Enum):
    CREATED = "created"
    SCREENED = "screened"
    PROPOSED = "proposed"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class TriggerType(str, Enum):
    WEBHOOK = "webhook"          # External system calls the agent
    SCHEDULED = "scheduled"      # Periodic check-in (cron-like)
    MANUAL = "manual"            # Human explicitly invokes
    FEEDBACK = "feedback"        # Revision requested via feedback loop
    API_CALL = "api_call"        # Another service/agent calls this agent


class TriggerSpec(BaseModel):
    """A trigger that causes a deployed agent to do work."""
    trigger_type: TriggerType
    name: str = Field(min_length=1)          # e.g., "Weekly check-in", "Slack webhook"
    config: dict = Field(default_factory=dict)  # type-specific config
    enabled: bool = True


class DeploymentTarget(str, Enum):
    """Where the agent can be deployed."""
    LOCAL = "local"              # Runs via psm invoke CLI / API
    AGENT_SDK = "agent_sdk"      # Claude Agent SDK
    AWS_LAMBDA = "aws_lambda"    # AWS Lambda function
    WEBHOOK_SERVICE = "webhook_service"  # Standalone webhook listener
    CUSTOM = "custom"            # User-defined deployment


class DeploymentConfig(BaseModel):
    """Runtime configuration for a specific deployment target."""
    target: DeploymentTarget = DeploymentTarget.LOCAL
    runtime_config: dict = Field(default_factory=dict)  # target-specific settings
    # e.g., for aws_lambda: {"function_name": "...", "region": "..."}
    # e.g., for agent_sdk: {"model": "claude-sonnet", "tools": [...]}
    # e.g., for local: {} (uses psm invoke)


class DeploymentSpec(BaseModel):
    """The portable blueprint for deploying a persistent agent.

    This is what the pipeline produces and a human reviews.
    It describes the agent, its job, its triggers, and where it can run.
    """

    spec_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)

    # Agent definition (frozen at proposal time)
    agent_name: str = Field(min_length=1)
    agent_title: str = Field(min_length=1)
    persona: str = Field(min_length=10)
    model: str = "claude-sonnet-4-20250514"

    # What this agent owns
    pattern_id: str = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=1)
    skills: list[dict] = Field(min_length=1)  # AgentSkill dicts

    # Job description
    responsibilities: list[str] = Field(min_length=1)
    triggers: list[TriggerSpec] = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)

    # Team integration
    assigned_to_role: str | None = None
    escalation_path: str | None = None

    # Deployment target
    deployment: DeploymentConfig = DeploymentConfig()

    # Lifecycle
    state: AgentLifecycleState = AgentLifecycleState.PROPOSED
    created_at: datetime = Field(default_factory=datetime.now)
    approved_at: datetime | None = None
    approved_by: str | None = None
    deployed_at: datetime | None = None
    retired_at: datetime | None = None
    retirement_reason: str | None = None

    # Screening results (embedded for reviewer convenience)
    screening_passed: bool = False
    screening_reason: str = ""
