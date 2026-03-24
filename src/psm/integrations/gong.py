"""Gong integration adapter.

Mock mode: reads from data/mock/gong_calls.json
Live mode: would use Gong REST API to fetch call transcripts
"""

from __future__ import annotations

import json
from datetime import datetime

from psm.config import settings
from psm.integrations.base import IntegrationAdapter
from psm.schemas.ingestion import IngestionRecord, SourceType


class GongAdapter(IntegrationAdapter):
    source_type = SourceType.GONG

    def test_connection(self) -> bool:
        if self.mock:
            return True
        # TODO: real Gong API auth check
        return False

    def fetch_records(self) -> list[IngestionRecord]:
        if self.mock:
            return self._fetch_mock()
        raise NotImplementedError("Live Gong integration not yet implemented")

    def _fetch_mock(self) -> list[IngestionRecord]:
        path = settings.mock_data_dir / "gong_calls.json"
        if not path.exists():
            return []
        calls = json.loads(path.read_text())
        records = []
        for call in calls:
            records.append(IngestionRecord(
                record_id=f"GONG-{call['call_id']}",
                source=SourceType.GONG,
                source_record_id=call["call_id"],
                raw_text=f"Call: {call['title']}\nParticipants: {', '.join(call['participants'])}\n\n{call['transcript_excerpt']}",
                metadata={
                    "title": call.get("title"),
                    "duration_minutes": call.get("duration_minutes"),
                    "sentiment": call.get("sentiment"),
                    "participants": call.get("participants"),
                },
                ingested_at=datetime.fromisoformat(call["date"].replace("Z", "+00:00")),
            ))
        return records
