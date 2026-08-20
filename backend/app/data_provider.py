import time
from datetime import datetime, timedelta

import httpx
import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Candle, DataCoverage

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

# Twelve Data intervals and their duration in seconds
INTERVAL_SECONDS = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "45min": 2700,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1day": 86400,
    "1week": 604800,
    "1month": 2629746,  # average month, only used to size chunks
}
SUPPORTED_INTERVALS = set(INTERVAL_SECONDS)

MAX_BARS_PER_REQUEST = 4500  # stay under Twelve Data's hard cap of 5000 per request


class DataProviderError(Exception):
    pass


def _upsert_candles(db: Session, rows: list[dict]) -> int:
    """Atomic INSERT ... ON CONFLICT DO NOTHING, keyed on the same
    (symbol, interval, timestamp) unique constraint as `uq_candle`.

    This used to be "query existing timestamps into a Python set, then
    insert whatever's missing" -- a classic check-then-act race: two
    concurrent requests fetching an overlapping range could both decide the
    same candle was missing and both try to insert it, one losing to a
    UniqueViolation. SQLite's single-writer lock mostly hid this; Postgres's
    real concurrent transactions do not. A single upsert statement is both
    race-free and faster (one round trip instead of one SELECT + N inserts).
    """
    if not rows:
        return 0
    dialect = db.get_bind().dialect.name
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert_fn(Candle).values(rows).on_conflict_do_nothing(index_elements=["symbol", "interval", "timestamp"])
    result = db.execute(stmt)
    db.commit()
    return result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(rows)


def _fetch_chunk(db: Session, symbol: str, interval: str, start: datetime, end: datetime) -> int:
    params = {
        "symbol": symbol,
        "interval": interval,
        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
        "apikey": settings.twelve_data_api_key,
        "format": "JSON",
        "order": "ASC",
        "outputsize": 5000,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.get(TWELVE_DATA_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    if payload.get("status") == "error":
        if payload.get("code") == 429:
            time.sleep(60)
            with httpx.Client(timeout=30) as client:
                resp = client.get(TWELVE_DATA_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
            if payload.get("status") == "error":
                raise DataProviderError(payload.get("message", "Unknown Twelve Data error"))
        else:
            raise DataProviderError(payload.get("message", "Unknown Twelve Data error"))

    values = payload.get("values", [])
    if not values:
        return 0

    rows = []
    for row in values:
        ts = (
            datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
            if len(row["datetime"]) > 10
            else datetime.strptime(row["datetime"], "%Y-%m-%d")
        )
        rows.append(
            {
                "symbol": symbol,
                "interval": interval,
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0) or None,
            }
        )

    return _upsert_candles(db, rows)


def _load_coverage(db: Session, symbol: str, interval: str) -> list[tuple[datetime, datetime]]:
    rows = (
        db.query(DataCoverage)
        .filter(DataCoverage.symbol == symbol, DataCoverage.interval == interval)
        .order_by(DataCoverage.range_start)
        .all()
    )
    return [(r.range_start, r.range_end) for r in rows]


def _missing_ranges(
    covered: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """Subtract already-covered ranges from [start, end], returning the gaps
    that still need to be fetched from the API."""
    gaps = []
    cursor = start
    for cov_start, cov_end in covered:
        if cov_end <= cursor or cov_start >= end:
            continue
        if cov_start > cursor:
            gaps.append((cursor, min(cov_start, end)))
        cursor = max(cursor, cov_end)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def _save_coverage(db: Session, symbol: str, interval: str, start: datetime, end: datetime) -> None:
    db.add(DataCoverage(symbol=symbol, interval=interval, range_start=start, range_end=end))
    db.flush()

    rows = (
        db.query(DataCoverage)
        .filter(DataCoverage.symbol == symbol, DataCoverage.interval == interval)
        .order_by(DataCoverage.range_start)
        .all()
    )
    merged: list[DataCoverage] = []
    for row in rows:
        if merged and row.range_start <= merged[-1].range_end:
            if row.range_end > merged[-1].range_end:
                merged[-1].range_end = row.range_end
            db.delete(row)
        else:
            merged.append(row)
    db.commit()


def fetch_and_cache(db: Session, symbol: str, interval: str, start: datetime, end: datetime) -> int:
    """Fetch OHLC candles from Twelve Data and upsert them into the local cache.

    Historical candles never change, so a `DataCoverage` table tracks which
    [start, end) windows have already been fetched for a symbol/interval.
    Repeat requests only hit the API for the parts of the range that aren't
    covered yet -- e.g. re-running a backtest on the same range, or widening
    an existing range, no longer re-downloads data we already have.

    Twelve Data also caps every request at 5000 candles, so any still-missing
    range is further split into multiple sequential requests here.

    Returns the number of new candles fetched from the API (not the total
    cached for the range).
    """
    if interval not in SUPPORTED_INTERVALS:
        raise DataProviderError(f"Unsupported interval '{interval}'. Supported: {sorted(SUPPORTED_INTERVALS)}")

    covered = _load_coverage(db, symbol, interval)
    gaps = _missing_ranges(covered, start, end)
    if not gaps:
        return 0

    if not settings.twelve_data_api_key:
        raise DataProviderError("TWELVE_DATA_API_KEY is not set. Copy .env.example to .env and add your key.")

    interval_seconds = INTERVAL_SECONDS[interval]
    chunk_span = timedelta(seconds=interval_seconds * MAX_BARS_PER_REQUEST)

    total = 0
    first = True
    for gap_start, gap_end in gaps:
        chunk_start = gap_start
        while chunk_start < gap_end:
            chunk_end = min(chunk_start + chunk_span, gap_end)
            if not first:
                time.sleep(1)  # be gentle with free-tier rate limits
            first = False
            total += _fetch_chunk(db, symbol, interval, chunk_start, chunk_end)
            chunk_start = chunk_end + timedelta(seconds=interval_seconds)
        _save_coverage(db, symbol, interval, gap_start, gap_end)

    return total


def load_candles(db: Session, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.interval == interval,
            Candle.timestamp >= start,
            Candle.timestamp <= end,
        )
        .order_by(Candle.timestamp.asc())
        .all()
    )
    df = pd.DataFrame(
        [
            {
                "time": r.timestamp,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    )
    return df
