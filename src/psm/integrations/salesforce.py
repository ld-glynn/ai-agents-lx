"""Salesforce integration adapter.

Mock mode: reads from data/mock/salesforce_cases.json
Live mode: would use simple-salesforce to query Case objects
"""

from __future__ import annotations

import json
from datetime import datetime

from psm.config import settings
from psm.integrations.base import IntegrationAdapter
from psm.schemas.ingestion import IngestionRecord, SourceType


class SalesforceAdapter(IntegrationAdapter):
    source_type = SourceType.SALESFORCE

    def test_connection(self) -> bool:
        if self.mock:
            return True
        # TODO: real Salesforce auth check
        return False

    def fetch_records(self) -> list[IngestionRecord]:
        if self.mock:
            return self._fetch_mock()
        # TODO: real Salesforce API call
        raise NotImplementedError("Live Salesforce integration not yet implemented")

    def _fetch_mock(self) -> list[IngestionRecord]:
        path = settings.mock_data_dir / "salesforce_cases.json"
        if not path.exists():
            return []
        cases = json.loads(path.read_text())
        records = []
        for case in cases:
            records.append(IngestionRecord(
                record_id=f"SF-{case['case_number']}",
                source=SourceType.SALESFORCE,
                source_record_id=case["case_number"],
                raw_text=f"{case['subject']}: {case['description']}",
                metadata={
                    "account_name": case.get("account_name"),
                    "priority": case.get("priority"),
                    "status": case.get("status"),
                },
                ingested_at=datetime.fromisoformat(case["created_date"].replace("Z", "+00:00")),
            ))
        return records
