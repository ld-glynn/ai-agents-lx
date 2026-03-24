"""Base adapter protocol for integration sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from psm.schemas.ingestion import IngestionRecord, IngestionSyncStatus, SourceType


class IntegrationAdapter(ABC):
    """Base class for all integration adapters.

    Each adapter can operate in mock mode (reads from data/mock/)
    or live mode (connects to the real API).
    """

    source_type: SourceType

    def __init__(self, mock: bool = True):
        self.mock = mock

    @abstractmethod
    def fetch_records(self) -> list[IngestionRecord]:
        """Fetch raw records from the source. Returns IngestionRecord objects."""
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the source is reachable. Returns True if connected."""
        ...

    def get_status(self) -> IngestionSyncStatus:
        """Return current sync status for this source."""
        try:
            connected = self.test_connection()
            return IngestionSyncStatus(
                source=self.source_type,
                enabled=True,
                status="mock" if self.mock else ("connected" if connected else "disconnected"),
            )
        except Exception as e:
            return IngestionSyncStatus(
                source=self.source_type,
                enabled=True,
                status="error",
                error_message=str(e),
            )
