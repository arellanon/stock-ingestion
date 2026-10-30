"""Immutable domain records. No ORM or provider SDK dependencies."""
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from decimal import Decimal
from typing import Any
from uuid import uuid4

INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "60m": 60, "1d": 1440}

class ProviderError(Exception):
    """Definitive provider failure; never retry automatically."""
class TransientError(ProviderError):
    """Temporary network/rate limit failure."""
class UnsupportedOperation(ProviderError):
    """Operation unavailable for this provider or entitlement."""

@dataclass(frozen=True)
class Instrument:
    id: str
    ticker: str
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Series:
    instrument_id: str
    provider: str
    feed: str
    interval: str
    adjustment: str
    session: str = "regular"
    def __post_init__(self):
        if self.interval not in INTERVALS: raise ValueError("Unsupported interval")
        if self.session not in {"regular", "extended"}: raise ValueError("Invalid session")
        if not all((self.instrument_id, self.provider, self.feed, self.adjustment)):
            raise ValueError("Series identity must be nonempty")

@dataclass(frozen=True)
class MarketBar:
    series: Series
    timestamp: datetime
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    complete: bool
    obtained_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class CorporateAction:
    instrument_id: str
    provider: str
    kind: str
    ex_date: date
    value: Decimal
    external_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Capabilities:
    intervals: tuple[str, ...]
    feeds: tuple[str, ...]
    adjustments: tuple[str, ...]
    retention_days: dict[str, int | None]
    chunk_days: dict[str, int]
    actions: bool
    metadata: bool
    notes: str

@dataclass(frozen=True)
class IngestionState:
    series: Series
    cursor: datetime | None

@dataclass(frozen=True)
class IngestionRun:
    id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "running"

@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    message: str
    timestamp: datetime | None = None

@dataclass(frozen=True)
class DataProvider:
    name: str

@dataclass(frozen=True)
class ProviderFeed:
    provider: str
    name: str
