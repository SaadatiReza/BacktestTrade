from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

Direction = Literal["bullish", "bearish"]


@dataclass
class Signal:
    """The minimal, strategy-agnostic contract every strategy must produce:
    where to enter, and where the setup is invalidated (stop-loss).

    Take-profit sizing, position management, and risk filtering are
    deliberately NOT decided here -- those are generic and applied
    uniformly by the backtest engine via ExecutionConfig, so any
    registered strategy is backtested under the same rules and results
    are comparable across strategies.

    Anything strategy-specific that's only useful for chart/table display
    (e.g. SP2L's spike/level markers) goes in `meta` as JSON-safe values
    (str/int/float/bool/None) -- the engine and DB never need to know what's
    inside it.
    """

    direction: Direction
    entry_idx: int
    entry_time: datetime
    entry_price: float
    sl_price: float
    meta: dict = field(default_factory=dict)


@dataclass
class SimulatedTrade:
    signal: Signal
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    exit_idx: Optional[int] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    r_multiple: Optional[float] = None
    result: Literal["win", "loss", "breakeven"] = "loss"
