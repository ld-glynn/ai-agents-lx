from __future__ import annotations

"""Skill executor — runs a skill on behalf of an Agent New Hire.

Each New Hire has a persona and context. When they use a skill,
the persona becomes the system prompt and the playbook guides the output.
"""

import json

from anthropic import Anthropic

from psm.schemas.agent import AgentNewHire, AgentSkill, SkillOutput
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern
from psm.tools.context_loader import load_playbook

# Map SkillType enum values to playbook file names
SKILL_TO_PLAYBOOK = {
    "recommend": "recommendation",
    "action_plan": "action_plan",
    "process_doc": "process_doc",
    "investigate": "investigation",
}


def run_skill(
    agent: AgentNewHire,
    skill: AgentSkill,
    hypothesis: Hypothesis,
    pattern: Pattern,
    model: str | None = None,
) -> SkillOutput:
    """Execute a skill on behalf of an Agent New Hire.

    The agent's persona becomes the system context. The skill's playbook
    provides the output structure.
    """
    client = Anthropic()
    use_model = model or agent.model

    playbook_name = SKILL_TO_PLAYBOOK.get(skill.skill_type.value, skill.skill_type.value)
    playbook = load_playbook(playbook_name)

    system_prompt = f"""You are {agent.name}, {agent.title}.

{agent.persona}

You are now using your "{skill.skill_type.value}" skill. Follow this playbook:

{playbook}

Return a JSON object with: "title" (short title for this deliverable),
"content" (the full deliverable text following the playbook structure),
and "next_steps" (array of concrete next action items).
Nothing else — no markdown fencing, no explanation outside the JSON."""

    user_content = json.dumps(
        {
            "hypothesis": hypothesis.model_dump(mode="json"),
            "pattern": pattern.model_dump(mode="json"),
        },
        indent=2,
    )

    response = client.messages.create(
        model=use_model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)

    return SkillOutput(
        agent_id=agent.agent_id,
        skill_type=skill.skill_type,
        hypothesis_id=skill.hypothesis_id,
        title=raw["title"],
        content=raw["content"],
        next_steps=raw.get("next_steps", []),
    )
