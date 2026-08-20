from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.timeutil import to_naive_utc


class SP2LConfig(BaseModel):
    """Setup-detection parameters for the SP2L strategy: everything that
    decides WHERE to enter and WHERE the setup is invalidated (stop-loss).

    Only the parts of SP2L that are corroborated by the strategy's own
    published outline and public write-ups are implemented here:
    Context -> Level -> Spike -> 2L (HL/LH) -> Entry -> SL.

    Explicitly NOT implemented (left as documented gaps, not guesses):
    the 4 Spike sub-types, P-Gap, and the 2X entry model. These are
    called out as dedicated, separate sections in the course outline and
    their exact rules must come from the source video, not be invented
    here. See backend/README.md.

    Take-profit sizing, holding period, and risk filtering are NOT here --
    those are strategy-agnostic and live in ExecutionConfig, applied
    uniformly by the backtest engine to every registered strategy.
    """

    # --- Spike detection ---
    spike_lookback: int = Field(3, ge=1, le=10, description="Bars spanning a candidate spike move")
    spike_atr_len: int = Field(14, ge=2, le=100)
    spike_atr_mult: float = Field(1.5, gt=0, description="Net move over spike_lookback bars must exceed ATR * mult")
    min_body_ratio: float = Field(0.65, ge=0, le=1, description="Avg body/range ratio required across spike bars (unverified default from public sources, not confirmed for this course)")

    # --- Context filter ---
    use_context_filter: bool = Field(True, description="Only take trades aligned with higher-timeframe trend")
    context_ema_len: int = Field(50, ge=2, le=400)

    # --- 2L structure ---
    swing_fractal_width: int = Field(2, ge=1, le=5, description="Bars on each side required to confirm a swing pivot")
    min_2l_swings: int = Field(2, ge=1, le=5, description="Minimum consecutive HL (bullish) / LH (bearish) swings required")
    structure_invalidation_buffer: float = Field(0.0, ge=0, description="Extra buffer beyond spike origin before structure is considered invalidated")

    # --- Entry ---
    entry_mode: Literal["retest", "breakout"] = Field("retest", description="Keep fixed across a single backtest run, per SP2L notes")
    require_rejection_candle: bool = Field(True, description="Retest mode only: require a rejection candle closing back in trend direction")

    # --- SL ---
    sl_buffer: float = Field(0.0, ge=0, description="Extra price buffer beyond the spike origin for stop placement")


class ExecutionConfig(BaseModel):
    """Generic trade-management rules applied by the backtest engine to
    every strategy's signals -- position sizing, TP, and holding rules are
    the same regardless of which strategy produced the entry/SL, so results
    across strategies stay comparable.
    """

    max_risk_price: Optional[float] = Field(None, gt=0, description="Skip a signal if its risk (entry-SL distance) exceeds this, in price units")
    tp_mode: Literal["single", "split"] = Field("split", description="single = one TP at rr_target; split = 50% at 1R, 50% at rr_target, SL to breakeven after 1R")
    rr_target: float = Field(2.0, gt=0, description="Reward:risk multiple for TP2 (or the sole TP in single mode)")
    max_holding_bars: int = Field(200, ge=1, description="Force-close a trade beyond this many bars, marking it win/loss/breakeven at that price")
    allow_concurrent_trades: bool = Field(False, description="If false, only one open trade at a time -- a new signal is skipped while a previous trade is still open")


class BacktestRequest(BaseModel):
    symbol: str = "XAU/USD"
    interval: str = "1h"
    start: datetime
    end: datetime
    strategy: str = "sp2l"
    strategy_config: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionConfig = ExecutionConfig()
    use_cached_data_only: bool = False

    @field_validator("start", "end")
    @classmethod
    def _strip_tz(cls, v: datetime) -> datetime:
        return to_naive_utc(v)


class FetchDataRequest(BaseModel):
    symbol: str = "XAU/USD"
    interval: str = "1h"
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _strip_tz(cls, v: datetime) -> datetime:
        return to_naive_utc(v)


class StrategyInfo(BaseModel):
    id: str
    name: str
    description: str
    config_schema: dict[str, Any]


class TradeOut(BaseModel):
    id: int
    direction: str
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    r_multiple: Optional[float]
    result: Optional[str]
    meta: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class BacktestStats(BaseModel):
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    winrate: float
    avg_r: float
    expectancy_r: float
    profit_factor: Optional[float]
    max_drawdown_r: float
    equity_curve_r: list[float]


class BacktestResponse(BaseModel):
    run_id: int
    symbol: str
    interval: str
    strategy: str
    stats: BacktestStats
    trades: list[TradeOut]
