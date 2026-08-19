from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SP2LConfig(BaseModel):
    """Parameters for the SP2L strategy.

    Only the parts of SP2L that are corroborated by the strategy's own
    published outline and public write-ups are implemented here:
    Context -> Level -> Spike -> 2L (HL/LH) -> Entry -> SL -> TP.

    Explicitly NOT implemented (left as documented gaps, not guesses):
    the 4 Spike sub-types, P-Gap, and the 2X entry model. These are
    called out as dedicated, separate sections in the course outline and
    their exact rules must come from the source video, not be invented
    here. See backend/README.md.
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

    # --- Risk / SL / TP ---
    sl_buffer: float = Field(0.0, ge=0, description="Extra price buffer beyond the spike origin for stop placement")
    max_risk_price: Optional[float] = Field(None, gt=0, description="Skip trade if risk (entry-SL distance) exceeds this, in price units")
    tp_mode: Literal["single", "split"] = Field("split", description="single = one TP at rr_target; split = 50% at 1R, 50% at rr_target, SL to breakeven after 1R")
    rr_target: float = Field(2.0, gt=0, description="Reward:risk multiple for TP2 (or the sole TP in single mode)")

    max_holding_bars: int = Field(200, ge=1, description="Force-close / mark as still-open beyond this many bars")
    allow_concurrent_trades: bool = Field(False, description="If false, only one open trade at a time")


class BacktestRequest(BaseModel):
    symbol: str = "XAU/USD"
    interval: str = "1h"
    start: datetime
    end: datetime
    config: SP2LConfig = SP2LConfig()
    use_cached_data_only: bool = False


class FetchDataRequest(BaseModel):
    symbol: str = "XAU/USD"
    interval: str = "1h"
    start: datetime
    end: datetime


class TradeOut(BaseModel):
    id: int
    direction: str
    context: Optional[str]
    entry_mode: str
    spike_start_time: Optional[datetime]
    spike_end_time: Optional[datetime]
    level_price: Optional[float]
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    r_multiple: Optional[float]
    result: Optional[str]

    model_config = {"from_attributes": True}


class BacktestStats(BaseModel):
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    open_trades: int
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
    stats: BacktestStats
    trades: list[TradeOut]
