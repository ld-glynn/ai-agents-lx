"""Local deployment adapter — agents run via psm invoke / API calls.

This is the default adapter. It doesn't provision external infrastructure.
Instead, agents are invoked on-demand through the CLI or API server.
The "deployment" is just marking the spec as deployed in the data store.
"""
from __future__ import annotations

import sys

from psm.schemas.deployment import DeploymentSpec, AgentLifecycleState


class LocalAdapter:
    """Deploy agents locally — they run via psm invoke or the API."""

    def deploy(self, spec: DeploymentSpec) -> dict:
        """Mark the agent as deployed. No external provisioning needed."""
        print(f"  [local-deploy] Agent '{spec.agent_name}' deployed locally", file=sys.stderr)
        print(f"  [local-deploy] Invoke via: psm invoke {spec.agent_id}", file=sys.stderr)
        print(f"  [local-deploy] Or via API: POST /api/agents/{spec.agent_id}/invoke", file=sys.stderr)

        return {
            "status": "deployed",
            "target": "local",
            "invoke_cli": f"psm invoke {spec.agent_id}",
            "invoke_api": f"POST /api/agents/{spec.agent_id}/invoke",
            "triggers_configured": [t.name for t in spec.triggers if t.enabled],
        }

    def teardown(self, spec: DeploymentSpec) -> dict:
        """No external teardown needed for local deployment."""
        return {"status": "torn_down", "target": "local"}

    def get_status(self, spec: DeploymentSpec) -> dict:
        """Check if the agent is deployed locally."""
        return {
            "status": spec.state.value if isinstance(spec.state, AgentLifecycleState) else spec.state,
            "target": "local",
            "agent_id": spec.agent_id,
        }
