from __future__ import annotations

"""Solver Router agent — maps hypotheses to solver types and team roles."""

import json

from anthropic import Anthropic

from psm.schemas.hypothesis import Hypothesis
from psm.schemas.solution import SolutionMapping
from psm.tools.context_loader import load_job_description, load_onboarding


def build_system_prompt() -> str:
    job_desc = load_job_description("solver_router")
    onboarding = load_onboarding()
    return f"""{job_desc}

## Team Context (from onboarding)
{onboarding}

You will receive hypotheses as JSON. For each one, produce a SolutionMapping.
Return a JSON array of SolutionMapping objects. Nothing else — no markdown, no explanation."""


def run_solver_router(
    hypotheses: list[Hypothesis],
    model: str = "claude-sonnet-4-20250514",
) -> list[SolutionMapping]:
    """Route hypotheses to solver types and team roles.

    Returns validated SolutionMapping objects.
    """
    client = Anthropic()

    user_content = json.dumps(
        [h.model_dump(mode="json") for h in hypotheses],
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
    mappings = [SolutionMapping.model_validate(m) for m in raw]

    # Validate referential integrity
    hyp_ids = {h.hypothesis_id for h in hypotheses}
    for m in mappings:
        if m.hypothesis_id not in hyp_ids:
            raise ValueError(
                f"Mapping {m.mapping_id} references non-existent hypothesis: {m.hypothesis_id}"
            )

    return mappings
