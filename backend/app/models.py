from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db import Base

# compiles to real JSONB (indexable, queryable) under Postgres, falls back
# to the generic text-serialized JSON under SQLite
json_variant = JSON().with_variant(JSONB, "postgresql")

# IMPORTANT: every timestamp column below is plain DateTime (timezone-naive)
# on purpose -- do NOT change any to DateTime(timezone=True). Candle
# timestamps are naive/UTC-implicit throughout the app (see
# app/timeutil.py's to_naive_utc, written after a real naive/aware
# comparison bug). Postgres TIMESTAMP WITHOUT TIME ZONE preserves that
# behavior exactly; TIMESTAMPTZ would silently reintroduce the same bug
# class across data_provider.py's range comparisons and anywhere
# candle/trade timestamps get compared.


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "timestamp", name="uq_candle"),
    )

    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True, nullable=False)
    interval = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)


class DataCoverage(Base):
    """Tracks which [range_start, range_end) windows have already been
    fetched from the data provider for a symbol/interval, so repeat
    requests for the same historical window never hit the API again.
    """

    __tablename__ = "data_coverage"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True, nullable=False)
    interval = Column(String, index=True, nullable=False)
    range_start = Column(DateTime, nullable=False)
    range_end = Column(DateTime, nullable=False)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    interval = Column(String, nullable=False)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    strategy_id = Column(String, index=True, nullable=False)
    config = Column(json_variant, nullable=False)  # {"strategy_config": {...}, "execution": {...}}
    stats = Column(json_variant, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    trades = relationship("Trade", back_populates="run", cascade="all, delete-orphan")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False)
    source = Column(String, default="backtest")  # backtest | live (reserved for later)

    direction = Column(String, nullable=False)  # bullish | bearish

    entry_time = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    sl_price = Column(Float, nullable=False)
    tp1_price = Column(Float, nullable=True)
    tp2_price = Column(Float, nullable=True)

    exit_time = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    r_multiple = Column(Float, nullable=True)
    result = Column(String, nullable=True)  # win | loss | breakeven

    # strategy-specific display extras (e.g. SP2L's spike/level markers).
    # opaque to the engine and DB on purpose -- each strategy defines its own shape.
    meta = Column(json_variant, nullable=True)

    run = relationship("BacktestRun", back_populates="trades")
