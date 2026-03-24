from __future__ import annotations

"""Hypothesis Generator agent — proposes testable solutions for patterns."""

import json

from anthropic import Anthropic

from psm.schemas.pattern import Pattern, ThemeSummary
from psm.schemas.hypothesis import Hypothesis
from psm.tools.context_loader import load_job_description


def build_system_prompt() -> str:
    job_desc = load_job_description("hypothesis_generator")
    return f"""{job_desc}

You will receive patterns and themes as JSON. For each pattern, propose 1-3 testable hypotheses.
Return a JSON array of Hypothesis objects. Nothing else — no markdown, no explanation."""


def run_hypothesis_generator(
    patterns: list[Pattern],
    themes: list[ThemeSummary],
    model: str = "claude-sonnet-4-20250514",
) -> list[Hypothesis]:
    """Generate hypotheses for identified patterns.

    Returns validated Hypothesis objects.
    """
    client = Anthropic()

    user_content = json.dumps(
        {
            "patterns": [p.model_dump(mode="json") for p in patterns],
            "themes": [t.model_dump(mode="json") for t in themes],
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
    hypotheses = [Hypothesis.model_validate(h) for h in raw]

    # Validate referential integrity
    pattern_ids = {p.pattern_id for p in patterns}
    for hyp in hypotheses:
        if hyp.pattern_id not in pattern_ids:
            raise ValueError(
                f"Hypothesis {hyp.hypothesis_id} references non-existent pattern: {hyp.pattern_id}"
            )

    return hypotheses
