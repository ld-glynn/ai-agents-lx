"""Hiring Manager — creates Agent New Hires for each problem cluster.

Replaces the old Solver Router. Instead of mapping hypotheses to generic solver types,
this agent designs specialized agents with personas and skill sets.
"""
from __future__ import annotations

import json

from anthropic import Anthropic

import re

from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern
from psm.schemas.agent import AgentNewHire, CaseFieldType
from psm.tools.context_loader import load_job_description, load_onboarding


def _recover_partial_json_array(text: str) -> list[dict]:
    """Try to recover valid objects from a truncated JSON array.

    Finds the last complete `}, {` boundary and closes the array there.
    """
    # Find all positions where a top-level object ends
    # Look for `}\n  ]` or `},` patterns at reasonable indent levels
    last_good = -1
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 1:  # closed a top-level array element
                last_good = i

    if last_good > 0:
        recovered = text[:last_good + 1] + "\n]"
        try:
            result = json.loads(recovered)
            print(f"  [hiring_manager] Recovered {len(result)} complete objects from truncated output")
            return result
        except json.JSONDecodeError:
            pass
    return []


# Runtime-managed fields that the Hiring Manager must NOT declare on a
# case_schema. The runtime adds these automatically; duplicating them in the
# domain schema would create conflicts at persistence time.
_RUNTIME_MANAGED_FIELD_NAMES = {
    "case_id",
    "created_at",
    "updated_at",
    "status",
    "audit_log",
    "lifecycle_state",
}


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
    config: dict | None = None,
) -> list[AgentNewHire]:
    """Create Agent New Hires — one per pattern, each with skills for their hypotheses.

    Returns validated AgentNewHire objects.
    """
    client = Anthropic()

    # Group hypotheses by pattern
    hyp_by_pattern: dict[str, list[Hypothesis]] = {}
    for hyp in hypotheses:
        hyp_by_pattern.setdefault(hyp.pattern_id, []).append(hyp)

    # Collect agent_ideas from discovered problems for each pattern
    from psm.tools.data_store import store as _store
    discovered = _store.read_discovered_problems()
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

    # Process in batches of 2 patterns to stay within token limits
    _BATCH_SIZE = 2
    system = build_system_prompt()
    agents: list[AgentNewHire] = []

    for batch_start in range(0, len(patterns), _BATCH_SIZE):
        batch_patterns = patterns[batch_start:batch_start + _BATCH_SIZE]
        batch_num = batch_start // _BATCH_SIZE + 1
        total_batches = (len(patterns) + _BATCH_SIZE - 1) // _BATCH_SIZE
        print(f"  [hiring_manager] Processing batch {batch_num}/{total_batches} ({len(batch_patterns)} patterns)...")
        try:
            from psm.agents.orchestrator import report_sub_progress
            report_sub_progress(f"Hiring Manager batch {batch_num}/{total_batches} ({len(batch_patterns)} patterns)")
        except ImportError:
            pass

        payload: dict = {
            "patterns": [p.model_dump(mode="json") for p in batch_patterns],
            "hypotheses_by_pattern": {
                pat.pattern_id: [h.model_dump(mode="json") for h in hyp_by_pattern.get(pat.pattern_id, [])]
                for pat in batch_patterns
            },
        }
        batch_ideas = {pid: ideas for pid, ideas in agent_ideas_by_pattern.items()
                       if any(p.pattern_id == pid for p in batch_patterns)}
        if batch_ideas:
            payload["agent_ideas_by_pattern"] = batch_ideas

        user_content = json.dumps(payload, indent=2)

        response = client.messages.create(
            model=model,
            max_tokens=16384,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  [hiring_manager] Batch {batch_num} JSON parse failed ({e}), attempting recovery...")
            raw = _recover_partial_json_array(text)
            if not raw:
                print(f"  [hiring_manager] Batch {batch_num} recovery failed, skipping batch")
                continue

        for a in raw:
            try:
                agents.append(AgentNewHire.model_validate(a))
            except Exception as e:
                name = a.get("name") or a.get("agent_id", "?")
                print(f"  [hiring_manager] Skipping invalid agent '{name}': {e}")

    print(f"  [hiring_manager] Total parsed: {len(agents)} agents")

    # Clean referential integrity: warn and skip agents with bad refs
    pattern_ids = {p.pattern_id for p in patterns}
    hyp_ids = {h.hypothesis_id for h in hypotheses}

    valid_agents = []
    for agent in agents:
        if agent.pattern_id not in pattern_ids:
            print(f"  [hiring_manager] Warning: {agent.agent_id} references unknown pattern {agent.pattern_id} — skipping")
            continue
        # Filter out bad hypothesis refs
        agent.hypothesis_ids = [h for h in agent.hypothesis_ids if h in hyp_ids]
        agent.skills = [s for s in agent.skills if s.hypothesis_id in hyp_ids]
        agent.job_functions = [jf for jf in agent.job_functions if jf.pattern_id in pattern_ids]
        for jf in agent.job_functions:
            jf.hypothesis_ids = [h for h in jf.hypothesis_ids if h in hyp_ids]
        # Drop agent if nothing left
        if not agent.hypothesis_ids or not agent.skills:
            print(f"  [hiring_manager] Warning: {agent.agent_id} has no valid hypotheses/skills after cleanup — skipping")
            continue
        # case_schema field integrity — runtime-managed fields are banned,
        # and enum fields must declare their values.
        for jf in agent.job_functions:
            for cf in jf.case_schema.fields:
                if cf.name.lower() in _RUNTIME_MANAGED_FIELD_NAMES:
                    print(f"  [hiring_manager] Warning: {agent.agent_id} declares runtime-managed field '{cf.name}' — skipping agent")
                    continue
                if cf.field_type == CaseFieldType.ENUM and not cf.enum_values:
                    print(f"  [hiring_manager] Warning: {agent.agent_id} enum field '{cf.name}' has no values — skipping agent")
                    continue
        valid_agents.append(agent)
    agents = valid_agents

    return agents
