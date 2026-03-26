"""Deployment adapters — translate specs into runtime configurations.

Each adapter takes a DeploymentSpec and produces whatever is needed
to run the agent in a specific environment.
"""

from psm.deploy.base import DeploymentAdapter
from psm.deploy.local import LocalAdapter

ADAPTERS = {
    "local": LocalAdapter,
}


def get_adapter(target: str = "local") -> DeploymentAdapter:
    cls = ADAPTERS.get(target)
    if not cls:
        raise ValueError(f"Unknown deployment target: {target}. Available: {list(ADAPTERS.keys())}")
    return cls()
