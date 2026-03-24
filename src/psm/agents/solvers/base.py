from __future__ import annotations

"""Base solver — all solver agents follow the same pattern."""

import json

from anthropic import Anthropic

from psm.schemas.solution import SolutionMapping, SolverOutput, SolverType
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern
from psm.tools.context_loader import load_playbook


def run_solver(
    mapping: SolutionMapping,
    hypothesis: Hypothesis,
    pattern: Pattern,
    model: str = "claude-haiku-4-5-20251001",
) -> SolverOutput:
    """Run a solver agent to produce a deliverable.

    Uses the playbook for the solver type as the system prompt.
    Uses Haiku for cost efficiency — solvers follow structured playbooks.
    """
    client = Anthropic()

    playbook = load_playbook(mapping.solver_type.value)

    system_prompt = f"""You are a solver agent. Follow this playbook exactly:

{playbook}

Produce the deliverable as plain text following the structure in the playbook.
Return a JSON object with: "title" (short title), "content" (the full deliverable text),
and "next_steps" (array of concrete next action items).
Nothing else — no markdown fencing, no explanation outside the JSON."""

    user_content = json.dumps(
        {
            "mapping": mapping.model_dump(mode="json"),
            "hypothesis": hypothesis.model_dump(mode="json"),
            "pattern": pattern.model_dump(mode="json"),
        },
        indent=2,
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)

    return SolverOutput(
        mapping_id=mapping.mapping_id,
        solver_type=mapping.solver_type,
        title=raw["title"],
        content=raw["content"],
        next_steps=raw.get("next_steps", []),
    )
