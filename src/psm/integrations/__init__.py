"""Integration adapters for external data sources."""

from psm.integrations.base import IntegrationAdapter
from psm.integrations.salesforce import SalesforceAdapter
from psm.integrations.gong import GongAdapter
from psm.integrations.slack import SlackAdapter

ADAPTERS = {
    "salesforce": SalesforceAdapter,
    "gong": GongAdapter,
    "slack": SlackAdapter,
}


def get_adapter(source: str, mock: bool = True) -> IntegrationAdapter:
    """Get an adapter instance by source name."""
    cls = ADAPTERS.get(source)
    if not cls:
        raise ValueError(f"Unknown source: {source}. Available: {list(ADAPTERS.keys())}")
    return cls(mock=mock)


def get_all_adapters(mock: bool = True) -> list[IntegrationAdapter]:
    """Get instances of all registered adapters."""
    return [cls(mock=mock) for cls in ADAPTERS.values()]
