"""Hypothesis Generator agent — proposes testable solutions for patterns."""
from __future__ import annotations

import json

from anthropic import Anthropic

from psm.schemas.pattern import Pattern, ThemeSummary
from psm.schemas.hypothesis import Hypothesis
from psm.tools.context_loader import load_job_description, load_onboarding
from psm.tools.data_store import store


def build_system_prompt() -> str:
    job_desc = load_job_description("hypothesis_generator")
    onboarding = load_onboarding()
    return f"""{job_desc}

## User Context
{onboarding}

You will receive patterns and themes as JSON. For each pattern, propose 1-3 testable hypotheses.
Return a JSON array of Hypothesis objects. Nothing else — no markdown, no explanation."""


def run_hypothesis_generator(
    patterns: list[Pattern],
    themes: list[ThemeSummary],
    model: str = "claude-sonnet-4-20250514",
    config: dict | None = None,
) -> list[Hypothesis]:
    """Generate hypotheses for identified patterns.

    Returns validated Hypothesis objects.
    """
    client = Anthropic()

    config_instructions = ""
    if config:
        parts = []
        if config.get("max_hypotheses_per_pattern"):
            parts.append(f"Generate at most {config['max_hypotheses_per_pattern']} hypotheses per pattern.")
        if config.get("confidence_floor"):
            parts.append(f"Only propose hypotheses with confidence >= {config['confidence_floor']}.")
        if config.get("effort_preference") and config["effort_preference"] != "any":
            parts.append(f"Prefer {config['effort_preference']}-effort solutions when possible.")
        if parts:
            config_instructions = "\n\nAdditional constraints:\n" + "\n".join(f"- {p}" for p in parts)

    # Collect agent_ideas from discovered problems for each pattern
    discovered = store.read_discovered_problems()
    problem_lookup = {p.id: p for p in discovered}
    agent_ideas_by_pattern: dict[str, list[str]] = {}
    for pat in patterns:
        ideas = []
        for pid in pat.problem_ids:
            prob = problem_lookup.get(pid)
            if prob and prob.agent_idea:
                ideas.append(prob.agent_idea)
        if ideas:
            agent_ideas_by_pattern[pat.pattern_id] = ideas

    payload: dict = {
        "patterns": [p.model_dump(mode="json") for p in patterns],
        "themes": [t.model_dump(mode="json") for t in themes],
    }
    if agent_ideas_by_pattern:
        payload["agent_ideas_by_pattern"] = agent_ideas_by_pattern

    user_content = json.dumps(payload, indent=2)

    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=build_system_prompt() + config_instructions,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)
    hypotheses = [Hypothesis.model_validate(h) for h in raw]

    # Clean referential integrity: drop hypotheses with invalid pattern refs
    pattern_ids = {p.pattern_id for p in patterns}
    valid = []
    for hyp in hypotheses:
        if hyp.pattern_id not in pattern_ids:
            print(f"  [hypothesis_gen] Warning: {hyp.hypothesis_id} references unknown pattern {hyp.pattern_id} — skipping")
        else:
            valid.append(hyp)

    return valid
