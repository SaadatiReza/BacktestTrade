import time
from datetime import datetime, timedelta

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Candle

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

    existing = {
        ts
        for (ts,) in db.query(Candle.timestamp).filter(
            Candle.symbol == symbol,
            Candle.interval == interval,
            Candle.timestamp >= start,
            Candle.timestamp <= end,
        )
    }

    count = 0
    for row in values:
        ts = (
            datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
            if len(row["datetime"]) > 10
            else datetime.strptime(row["datetime"], "%Y-%m-%d")
        )
        if ts in existing:
            continue
        db.add(
            Candle(
                symbol=symbol,
                interval=interval,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0) or None,
            )
        )
        existing.add(ts)
        count += 1
    db.commit()
    return count


def fetch_and_cache(db: Session, symbol: str, interval: str, start: datetime, end: datetime) -> int:
    """Fetch OHLC candles from Twelve Data and upsert them into the local cache.

    Twelve Data caps every request at 5000 candles, so a wide date range on a
    low timeframe (e.g. 60 days of 1min bars = ~86k bars) is split into
    multiple sequential requests here. Without this, older parts of a
    requested range would silently never get fetched.

    Returns the number of new candles fetched from the API (not the total
    cached for the range).
    """
    if interval not in SUPPORTED_INTERVALS:
        raise DataProviderError(f"Unsupported interval '{interval}'. Supported: {sorted(SUPPORTED_INTERVALS)}")
    if not settings.twelve_data_api_key:
        raise DataProviderError("TWELVE_DATA_API_KEY is not set. Copy .env.example to .env and add your key.")

    interval_seconds = INTERVAL_SECONDS[interval]
    chunk_span = timedelta(seconds=interval_seconds * MAX_BARS_PER_REQUEST)

    total = 0
    chunk_start = start
    first = True
    while chunk_start < end:
        chunk_end = min(chunk_start + chunk_span, end)
        if not first:
            time.sleep(1)  # be gentle with free-tier rate limits
        first = False
        total += _fetch_chunk(db, symbol, interval, chunk_start, chunk_end)
        chunk_start = chunk_end + timedelta(seconds=interval_seconds)

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
