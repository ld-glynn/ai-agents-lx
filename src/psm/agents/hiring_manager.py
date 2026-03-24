from __future__ import annotations

"""Hiring Manager — creates Agent New Hires for each problem cluster.

Replaces the old Solver Router. Instead of mapping hypotheses to generic solver types,
this agent designs specialized agents with personas and skill sets.
"""

import json

from anthropic import Anthropic

from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern
from psm.schemas.agent import AgentNewHire
from psm.tools.context_loader import load_job_description, load_onboarding


def build_system_prompt() -> str:
    job_desc = load_job_description("hiring_manager")
    onboarding = load_onboarding()
    return f"""{job_desc}

## Team Context (from onboarding)
{onboarding}

You will receive patterns and their associated hypotheses as JSON.
For each pattern, create one AgentNewHire.
Return a JSON array of AgentNewHire objects. Nothing else — no markdown, no explanation."""


def run_hiring_manager(
    patterns: list[Pattern],
    hypotheses: list[Hypothesis],
    model: str = "claude-sonnet-4-20250514",
) -> list[AgentNewHire]:
    """Create Agent New Hires — one per pattern, each with skills for their hypotheses.

    Returns validated AgentNewHire objects.
    """
    client = Anthropic()

    # Group hypotheses by pattern
    hyp_by_pattern: dict[str, list[Hypothesis]] = {}
    for hyp in hypotheses:
        hyp_by_pattern.setdefault(hyp.pattern_id, []).append(hyp)

    user_content = json.dumps(
        {
            "patterns": [p.model_dump(mode="json") for p in patterns],
            "hypotheses_by_pattern": {
                pid: [h.model_dump(mode="json") for h in hyps]
                for pid, hyps in hyp_by_pattern.items()
            },
        },
        indent=2,
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=build_system_prompt(),
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)
    agents = [AgentNewHire.model_validate(a) for a in raw]

    # Validate referential integrity
    pattern_ids = {p.pattern_id for p in patterns}
    hyp_ids = {h.hypothesis_id for h in hypotheses}

    for agent in agents:
        if agent.pattern_id not in pattern_ids:
            raise ValueError(
                f"Agent {agent.agent_id} references non-existent pattern: {agent.pattern_id}"
            )
        for hid in agent.hypothesis_ids:
            if hid not in hyp_ids:
                raise ValueError(
                    f"Agent {agent.agent_id} references non-existent hypothesis: {hid}"
                )
        for skill in agent.skills:
            if skill.hypothesis_id not in hyp_ids:
                raise ValueError(
                    f"Agent {agent.agent_id} skill references non-existent hypothesis: {skill.hypothesis_id}"
                )

    return agents
