"""Slack integration adapter.

Mock mode: reads from data/mock/slack_messages.json
Live mode: would use Slack SDK to fetch messages from configured channels
"""

from __future__ import annotations

import json
from datetime import datetime

from psm.config import settings
from psm.integrations.base import IntegrationAdapter
from psm.schemas.ingestion import IngestionRecord, SourceType


class SlackAdapter(IntegrationAdapter):
    source_type = SourceType.SLACK

    def test_connection(self) -> bool:
        if self.mock:
            return True
        # TODO: real Slack auth check
        return False

    def fetch_records(self) -> list[IngestionRecord]:
        if self.mock:
            return self._fetch_mock()
        raise NotImplementedError("Live Slack integration not yet implemented")

    def _fetch_mock(self) -> list[IngestionRecord]:
        path = settings.mock_data_dir / "slack_messages.json"
        if not path.exists():
            return []
        messages = json.loads(path.read_text())
        records = []
        for msg in messages:
            records.append(IngestionRecord(
                record_id=f"SLACK-{msg['channel']}-{msg['timestamp'].replace(':', '').replace('-', '')}",
                source=SourceType.SLACK,
                source_record_id=msg.get("thread_ts"),
                raw_text=f"[{msg['channel']}] {msg['user']}: {msg['text']}",
                metadata={
                    "channel": msg.get("channel"),
                    "user": msg.get("user"),
                    "thread_replies": msg.get("thread_replies"),
                },
                ingested_at=datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00")),
            ))
        return records
