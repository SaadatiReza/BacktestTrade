from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

Direction = Literal["bullish", "bearish"]


@dataclass
class Spike:
    direction: Direction
    start_idx: int
    end_idx: int
    start_time: datetime
    end_time: datetime
    origin_price: float  # extreme price before the spike started (used for SL)
    end_price: float


@dataclass
class Swing:
    idx: int
    time: datetime
    price: float
    kind: Literal["high", "low"]


@dataclass
class Structure2L:
    spike: Spike
    swings: list[Swing]  # consecutive HL (bullish) or LH (bearish) swings
    level_price: float  # most recent swing used as the retest/breakout level
    level_time: datetime
    structure_high: float  # highest high since spike (breakout reference, bullish)
    structure_low: float  # lowest low since spike (breakout reference, bearish)


@dataclass
class Signal:
    direction: Direction
    entry_mode: Literal["retest", "breakout"]
    context: Optional[str]
    structure: Structure2L
    entry_idx: int
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp1_price: Optional[float]
    tp2_price: Optional[float]


@dataclass
class SimulatedTrade:
    signal: Signal
    exit_idx: Optional[int] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    r_multiple: Optional[float] = None
    result: Literal["win", "loss", "breakeven", "open", "invalid"] = "open"
