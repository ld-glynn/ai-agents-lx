from __future__ import annotations

"""Base deployment adapter interface.

Each adapter translates a DeploymentSpec into a specific runtime.
The adapter handles: provisioning, configuration, and teardown.
"""

from abc import ABC, abstractmethod

from psm.schemas.deployment import DeploymentSpec


class DeploymentAdapter(ABC):
    """Base class for deployment target adapters."""

    @abstractmethod
    def deploy(self, spec: DeploymentSpec) -> dict:
        """Deploy an agent from a spec. Returns deployment metadata."""
        ...

    @abstractmethod
    def teardown(self, spec: DeploymentSpec) -> dict:
        """Tear down a deployed agent. Returns teardown metadata."""
        ...

    @abstractmethod
    def get_status(self, spec: DeploymentSpec) -> dict:
        """Check the deployment status of an agent."""
        ...

    def export_config(self, spec: DeploymentSpec) -> dict:
        """Export the deployment configuration as a portable dict.

        This can be used to manually configure an external system
        (AWS Lambda, Agent SDK, etc.) without the adapter doing it automatically.
        """
        return {
            "agent_id": spec.agent_id,
            "agent_name": spec.agent_name,
            "persona": spec.persona,
            "model": spec.model,
            "skills": spec.skills,
            "triggers": [t.model_dump() for t in spec.triggers if t.enabled],
            "responsibilities": spec.responsibilities,
            "pattern_id": spec.pattern_id,
            "hypothesis_ids": spec.hypothesis_ids,
            "deployment_target": spec.deployment.target.value,
            "runtime_config": spec.deployment.runtime_config,
        }
