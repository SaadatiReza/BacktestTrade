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
from sqlalchemy.orm import relationship

from app.db import Base


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


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    interval = Column(String, nullable=False)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    config = Column(JSON, nullable=False)
    stats = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    trades = relationship("Trade", back_populates="run", cascade="all, delete-orphan")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False)
    source = Column(String, default="backtest")  # backtest | live (reserved for later)

    direction = Column(String, nullable=False)  # bullish | bearish
    context = Column(String, nullable=True)
    entry_mode = Column(String, nullable=False)  # retest | breakout

    spike_start_time = Column(DateTime, nullable=True)
    spike_end_time = Column(DateTime, nullable=True)
    level_price = Column(Float, nullable=True)

    entry_time = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    sl_price = Column(Float, nullable=False)
    tp1_price = Column(Float, nullable=True)
    tp2_price = Column(Float, nullable=True)

    exit_time = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    r_multiple = Column(Float, nullable=True)
    result = Column(String, nullable=True)  # win | loss | breakeven | open | invalid

    run = relationship("BacktestRun", back_populates="trades")
