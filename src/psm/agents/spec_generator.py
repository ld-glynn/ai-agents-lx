from __future__ import annotations

"""Spec Generator — Stage 5: Produce deployment specifications.

Takes screened AgentNewHire candidates and generates DeploymentSpec
blueprints describing how each agent should be deployed as a persistent worker.

The specs are deployment-target agnostic — they describe what the agent does,
not where it runs. A DeploymentAdapter later translates specs into runtime config.
"""

import sys
from datetime import datetime

from psm.schemas.agent import AgentNewHire
from psm.schemas.pattern import Pattern
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.deployment import (
    DeploymentSpec, TriggerSpec, TriggerType,
    DeploymentConfig, DeploymentTarget, AgentLifecycleState,
)


def _log(msg: str) -> None:
    print(f"  [spec_generator] {msg}", file=sys.stderr)


# Responsibility templates by skill type
_RESPONSIBILITY_TEMPLATES = {
    "recommend": "Produce and maintain recommendations for: {outcome}",
    "action_plan": "Maintain implementation plan for: {outcome}",
    "process_doc": "Draft and update process documentation for: {outcome}",
    "investigate": "Conduct ongoing investigation into: {outcome}",
}

# Expected output templates by skill type
_OUTPUT_TEMPLATES = {
    "recommend": "Recommendation document with rationale, tradeoffs, and next steps",
    "action_plan": "Phased implementation plan with timelines, owners, and success metrics",
    "process_doc": "Operational documentation (runbooks, playbooks, SOPs)",
    "investigate": "Investigation report with root cause analysis and findings",
}


def generate_deployment_specs(
    agents: list[AgentNewHire],
    patterns: list[Pattern],
    hypotheses: list[Hypothesis],
) -> list[DeploymentSpec]:
    """Generate deployment specs for screened agent candidates.

    Each spec describes the agent's ongoing job: what it owns, what triggers
    it to work, and what outputs it produces.
    """
    pat_by_id = {p.pattern_id: p for p in patterns}
    hyp_by_id = {h.hypothesis_id: h for h in hypotheses}

    specs = []
    for agent in agents:
        pattern = pat_by_id.get(agent.pattern_id)
        if not pattern:
            _log(f"WARNING: Pattern {agent.pattern_id} not found for {agent.name}, skipping")
            continue

        # Build responsibilities from skills + hypotheses
        responsibilities = []
        expected_outputs = []
        for skill in agent.skills:
            hyp = hyp_by_id.get(skill.hypothesis_id)
            outcome = hyp.expected_outcome if hyp else "assigned hypothesis"

            template = _RESPONSIBILITY_TEMPLATES.get(skill.skill_type.value, "Work on: {outcome}")
            responsibilities.append(template.format(outcome=outcome))

            output_desc = _OUTPUT_TEMPLATES.get(skill.skill_type.value, "Deliverable document")
            expected_outputs.append(f"{skill.skill_type.value}: {output_desc}")

        # Add a general ownership responsibility
        responsibilities.insert(0, f"Own and monitor the '{pattern.name}' problem area ({len(pattern.problem_ids)} problems)")

        # Default triggers — operational, not problem-driven
        triggers = [
            TriggerSpec(
                trigger_type=TriggerType.MANUAL,
                name="Manual invocation",
                config={},
                enabled=True,
            ),
            TriggerSpec(
                trigger_type=TriggerType.SCHEDULED,
                name="Weekly check-in",
                config={"interval_hours": 168, "cron": "0 9 * * 1"},  # Monday 9am
                enabled=False,  # Disabled by default, human enables
            ),
            TriggerSpec(
                trigger_type=TriggerType.WEBHOOK,
                name="External webhook",
                config={"endpoint": f"/api/agents/{agent.agent_id}/webhook"},
                enabled=False,
            ),
            TriggerSpec(
                trigger_type=TriggerType.FEEDBACK,
                name="Revision requested",
                config={},
                enabled=True,
            ),
        ]

        spec = DeploymentSpec(
            spec_id=f"SPEC-{agent.agent_id}",
            agent_id=agent.agent_id,
            agent_name=agent.name,
            agent_title=agent.title,
            persona=agent.persona,
            model=agent.model,
            pattern_id=agent.pattern_id,
            hypothesis_ids=agent.hypothesis_ids,
            skills=[s.model_dump(mode="json") for s in agent.skills],
            responsibilities=responsibilities,
            triggers=triggers,
            expected_outputs=expected_outputs,
            assigned_to_role=agent.assigned_to_role,
            deployment=DeploymentConfig(target=DeploymentTarget.LOCAL),
            state=AgentLifecycleState.PROPOSED,
            screening_passed=True,
            screening_reason="Passed automated screening gate",
        )

        specs.append(spec)
        _log(f"Generated spec for {agent.name}: {len(responsibilities)} responsibilities, {len(triggers)} triggers")

    return specs
