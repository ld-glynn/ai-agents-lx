from __future__ import annotations
"""Ingestion schemas — upstream of RawProblem.

IngestionRecord: raw data fetched from an external source (Salesforce, Gong, Slack).
The Structurer agent converts these into RawProblem objects for the pipeline.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    SALESFORCE = "salesforce"
    GONG = "gong"
    SLACK = "slack"
    CSV = "csv"
    MANUAL = "manual"


class IngestionRecord(BaseModel):
    """A raw data record fetched from an external source."""

    model_config = {"strict": True}

    record_id: str = Field(min_length=1)
    source: SourceType
    source_record_id: str | None = None
    raw_text: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)
    ingested_at: datetime = Field(default_factory=datetime.now)
    structured: bool = False
    extracted_problem_ids: list[str] = Field(default_factory=list)


class IngestionSyncStatus(BaseModel):
    """Tracks the connection and sync state of an integration source."""

    model_config = {"strict": True}

    source: SourceType
    enabled: bool = True
    status: str = "disconnected"  # connected, disconnected, error, mock
    last_sync_at: datetime | None = None
    record_count: int = 0
    error_message: str | None = None
