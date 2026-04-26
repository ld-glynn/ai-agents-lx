"""Agent Invoker — trigger deployed agents to do work.

Handles the full invocation lifecycle:
1. Find the agent's deployment spec
2. Create a work log entry
3. Execute the relevant skill(s)
4. Store outputs and update the work log
5. Update agent lifecycle state
"""
from __future__ import annotations

import sys
from datetime import datetime

from psm.tools.data_store import store
from psm.agents.solvers.base import run_skill
from psm.schemas.deployment import AgentLifecycleState, TriggerType
from psm.schemas.work_log import WorkLogEntry


def _log(msg: str) -> None:
    print(f"[invoker] {msg}", file=sys.stderr)


def invoke_agent(
    agent_id: str,
    trigger_type: TriggerType,
    trigger_detail: str,
    *,
    model: str = "claude-sonnet-4-20250514",
) -> list[WorkLogEntry]:
    """Invoke a deployed agent to do work.

    Executes all pending/active skills and creates work log entries for each.
    Returns the work log entries created.
    """
    # Load the deployment spec
    specs = store.read_deployment_specs()
    spec = next((s for s in specs if s.agent_id == agent_id), None)
    if not spec:
        raise ValueError(f"No deployment spec found for agent: {agent_id}")

    if spec.state not in (AgentLifecycleState.DEPLOYED.value, AgentLifecycleState.DEPLOYED, "deployed"):
        raise ValueError(f"Agent {agent_id} is not deployed (state: {spec.state})")

    # Load context
    agents = store.read_new_hires()
    agent = next((a for a in agents if a.agent_id == agent_id), None)
    if not agent:
        raise ValueError(f"Agent data not found: {agent_id}")

    patterns = store.read_patterns()
    hypotheses = store.read_hypotheses()
    pat_by_id = {p.pattern_id: p for p in patterns}
    hyp_by_id = {h.hypothesis_id: h for h in hypotheses}

    pattern = pat_by_id.get(agent.pattern_id)
    if not pattern:
        raise ValueError(f"Pattern {agent.pattern_id} not found for agent {agent.name}")

    # Determine invocation number
    existing_entries = store.read_work_log(agent_id)
    invocation_number = len(existing_entries) + 1

    # Update spec state to active
    store.update_deployment_spec(spec.spec_id, {"state": "active"})

    _log(f"Invoking {agent.name} (invocation #{invocation_number}, trigger: {trigger_type.value})")

    entries = []
    for skill in sorted(agent.skills, key=lambda s: s.priority):
        hyp = hyp_by_id.get(skill.hypothesis_id)
        if not hyp:
            _log(f"  WARNING: Hypothesis {skill.hypothesis_id} not found, skipping")
            continue

        entry_id = f"WL-{agent_id}-{invocation_number}-{skill.skill_type.value}"

        # Create work log entry
        entry = WorkLogEntry(
            entry_id=entry_id,
            agent_id=agent_id,
            spec_id=spec.spec_id,
            trigger_type=trigger_type,
            trigger_detail=trigger_detail,
            skill_type=skill.skill_type,
            hypothesis_id=skill.hypothesis_id,
            action_summary=f"Executing {skill.skill_type.value} for {hyp.expected_outcome[:60]}",
            invocation_number=invocation_number,
        )

        try:
            _log(f"  Executing {skill.skill_type.value} for {skill.hypothesis_id}")
            output = run_skill(agent, skill, hyp, pattern, model=model)

            # Link output to work log
            output.work_log_entry_id = entry_id
            output.invocation_number = invocation_number
            store.append_skill_output(output)

            # Update entry as completed
            entry.status = "completed"
            entry.completed_at = datetime.now()
            entry.output_id = entry_id
            entry.action_summary = f"Produced {skill.skill_type.value}: {output.title}"

        except Exception as e:
            _log(f"  FAILED: {e}")
            entry.status = "failed"
            entry.completed_at = datetime.now()
            entry.error_message = str(e)

        store.append_work_log_entry(agent_id, entry)
        entries.append(entry)

    # Update spec state back to deployed
    store.update_deployment_spec(spec.spec_id, {"state": "deployed"})

    _log(f"Invocation complete: {len(entries)} skills executed")
    return entries


def deploy_agent(spec_id: str, *, model: str = "claude-sonnet-4-20250514") -> dict:
    """Deploy an approved agent and run its initial invocation.

    Transitions: approved -> deployed, then invokes the agent.
    """
    spec = store.get_deployment_spec(spec_id)
    if not spec:
        raise ValueError(f"Deployment spec not found: {spec_id}")

    if spec.state not in (AgentLifecycleState.APPROVED.value, AgentLifecycleState.APPROVED, "approved"):
        raise ValueError(f"Spec {spec_id} is not approved (state: {spec.state})")

    # Transition to deployed
    store.update_deployment_spec(spec_id, {
        "state": "deployed",
        "deployed_at": datetime.now().isoformat(),
    })

    # Update agent lifecycle
    agents = store.read_new_hires()
    for a in agents:
        if a.agent_id == spec.agent_id:
            a.lifecycle_state = "deployed"
            a.deployment_spec_id = spec_id
    store.write_new_hires(agents)

    _log(f"Agent '{spec.agent_name}' deployed via spec {spec_id}")

    # Run initial invocation
    from psm.deploy import get_adapter
    adapter = get_adapter(spec.deployment.target.value)
    deploy_result = adapter.deploy(spec)

    entries = invoke_agent(
        spec.agent_id,
        TriggerType.MANUAL,
        "Initial deployment invocation",
        model=model,
    )

    return {
        "status": "deployed",
        "spec_id": spec_id,
        "agent_id": spec.agent_id,
        "deploy_result": deploy_result,
        "initial_invocation_entries": len(entries),
    }


def pause_agent(agent_id: str) -> dict:
    """Pause a deployed agent."""
    specs = store.read_deployment_specs()
    spec = next((s for s in specs if s.agent_id == agent_id), None)
    if not spec:
        raise ValueError(f"No spec found for agent: {agent_id}")
    store.update_deployment_spec(spec.spec_id, {"state": "paused"})
    return {"status": "paused", "agent_id": agent_id}


def resume_agent(agent_id: str) -> dict:
    """Resume a paused agent."""
    specs = store.read_deployment_specs()
    spec = next((s for s in specs if s.agent_id == agent_id), None)
    if not spec:
        raise ValueError(f"No spec found for agent: {agent_id}")
    store.update_deployment_spec(spec.spec_id, {"state": "deployed"})
    return {"status": "deployed", "agent_id": agent_id}


def retire_agent(agent_id: str, reason: str = "") -> dict:
    """Retire an agent permanently."""
    specs = store.read_deployment_specs()
    spec = next((s for s in specs if s.agent_id == agent_id), None)
    if not spec:
        raise ValueError(f"No spec found for agent: {agent_id}")
    store.update_deployment_spec(spec.spec_id, {
        "state": "retired",
        "retired_at": datetime.now().isoformat(),
        "retirement_reason": reason,
    })
    # Update agent lifecycle
    agents = store.read_new_hires()
    for a in agents:
        if a.agent_id == agent_id:
            a.lifecycle_state = "retired"
    store.write_new_hires(agents)
    return {"status": "retired", "agent_id": agent_id, "reason": reason}
