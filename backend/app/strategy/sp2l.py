"""SP2L strategy detection: Context -> Level -> Spike -> 2L -> Entry -> SL.

This module implements ONLY the parts of SP2L that are corroborated across
the course's public outline and general SP2L write-ups:
  - Spike detection (a fast, imbalanced directional move)
  - 2L structure: a sequence of Higher-Lows (bullish) / Lower-Highs (bearish)
    following the spike, each acting as a potential Level
  - Context filter (higher-timeframe trend alignment)
  - Entry via Retest of a Level, or Breakout of structure (pick one per run)
  - SL beyond the spike origin

Deliberately NOT implemented (see backend/README.md): the 4 Spike sub-types,
P-Gap, and the 2X entry model. These are separate, dedicated sections in the
course outline whose exact rules are not reliably documented in public
sources. Rather than guess and silently bake in a wrong rule, these are left
as explicit gaps to fill in once verified against the source video.

Take-profit sizing, position management, and risk filtering are NOT decided
here -- those are generic and applied by the backtest engine via
ExecutionConfig, uniformly across every registered strategy. This module
only ever emits a Signal: where to enter, where the setup is invalidated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd

from app.schemas import SP2LConfig
from app.strategy.base import StrategyDefinition, register_strategy
from app.strategy.types import Direction, Signal


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


def compute_atr(df: pd.DataFrame, length: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(length, min_periods=length).mean()


def compute_swings(df: pd.DataFrame, width: int) -> list[Swing]:
    window = 2 * width + 1
    roll_max = df["high"].rolling(window, center=True, min_periods=window).max()
    roll_min = df["low"].rolling(window, center=True, min_periods=window).min()
    is_high = (df["high"] == roll_max).fillna(False).to_numpy()
    is_low = (df["low"] == roll_min).fillna(False).to_numpy()

    # tag 0 = high, 1 = low: replicates the original's per-bar append order
    # (high before low) under a stable sort by idx
    records = [(i, 0) for i in np.flatnonzero(is_high)] + [(i, 1) for i in np.flatnonzero(is_low)]
    records.sort(key=lambda r: (r[0], r[1]))

    # keep pandas-native Timestamp objects (not numpy.datetime64) via .iat --
    # only over the small number of actual swings, not every bar
    swings: list[Swing] = []
    for idx, tag in records:
        if tag == 0:
            swings.append(Swing(idx=int(idx), time=df["time"].iat[idx], price=float(df["high"].iat[idx]), kind="high"))
        else:
            swings.append(Swing(idx=int(idx), time=df["time"].iat[idx], price=float(df["low"].iat[idx]), kind="low"))
    return swings


def compute_context(df: pd.DataFrame, ema_len: int) -> pd.Series:
    ema = df["close"].ewm(span=ema_len, adjust=False).mean()
    slope = ema.diff(5)

    # NaN comparisons (EMA warmup) evaluate False on both branches, falling
    # through to "range" -- matches the original's explicit pd.isna() check
    up = (df["close"] > ema) & (slope > 0)
    down = (df["close"] < ema) & (slope < 0)
    return pd.Series(np.select([up, down], ["uptrend", "downtrend"], default="range"), index=df.index)


def detect_spikes(df: pd.DataFrame, atr: pd.Series, config: SP2LConfig) -> list[Spike]:
    n = len(df)
    lookback = config.spike_lookback

    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    atr_arr = atr.to_numpy()

    # net_move for the trailing `lookback`-bar window ending at i is
    # close[i] - open[i-lookback+1]
    net_move = close - df["open"].shift(lookback - 1).to_numpy()

    range_arr = high - low
    body_arr = np.abs(close - open_)
    # np.nan (not pd.NA): pd.NA over the *full* series would force an
    # object-dtype column and defeat the point of vectorizing
    with np.errstate(divide="ignore", invalid="ignore"):
        body_ratio_bar = np.where(range_arr == 0, np.nan, body_arr / range_arr)
    # pandas rolling already skips NaN within the window when counting
    # min_periods, so min_periods=1 reproduces the original's mean(skipna=True)
    avg_body_ratio = pd.Series(body_ratio_bar).rolling(lookback, min_periods=1).mean().to_numpy()

    positions = np.arange(n)
    start_ok = (positions >= config.spike_atr_len) & (positions >= lookback - 1)
    atr_ok = ~np.isnan(atr_arr) & (atr_arr > 0)
    move_ok = np.abs(net_move) >= atr_arr * config.spike_atr_mult
    body_ok = ~np.isnan(avg_body_ratio) & (avg_body_ratio >= config.min_body_ratio)
    candidate_idxs = np.flatnonzero(start_ok & atr_ok & move_ok & body_ok)

    # each candidate's status depends only on its own trailing window, not on
    # whether earlier bars were skipped -- so the sequential "jump past this
    # move" behavior can be replicated with a small loop over just the
    # (much rarer) candidates instead of every bar
    spikes: list[Spike] = []
    pointer = config.spike_atr_len
    for i in candidate_idxs:
        i = int(i)
        if i < pointer:
            continue
        lo = i - lookback + 1
        direction = "bullish" if net_move[i] > 0 else "bearish"
        origin_price = float(low[lo : i + 1].min()) if direction == "bullish" else float(high[lo : i + 1].max())
        spikes.append(
            Spike(
                direction=direction,
                start_idx=lo,
                end_idx=i,
                start_time=df["time"].iat[lo],
                end_time=df["time"].iat[i],
                origin_price=origin_price,
                end_price=float(close[i]),
            )
        )
        pointer = i + lookback
    return spikes


def find_2l_structure(df: pd.DataFrame, spike: Spike, swings: list[Swing], config: SP2LConfig) -> Structure2L | None:
    kind = "low" if spike.direction == "bullish" else "high"
    relevant = [s for s in swings if s.idx > spike.end_idx and s.kind == kind]

    accepted: list[Swing] = []
    last_price = spike.origin_price
    origin_bound = (
        spike.origin_price - config.structure_invalidation_buffer
        if spike.direction == "bullish"
        else spike.origin_price + config.structure_invalidation_buffer
    )

    # find the first bar after the spike that breaches the origin bound ONCE
    # (a single numpy pass) instead of re-scanning the whole dataframe for
    # every candidate swing -- a structure that never resolves would
    # otherwise pay a full-dataframe scan per remaining swing until
    # end-of-data
    idx_arr = df["idx"].to_numpy()
    close_arr = df["close"].to_numpy()
    tail_mask = idx_arr > spike.end_idx
    if spike.direction == "bullish":
        viol_mask = tail_mask & (close_arr < origin_bound)
    else:
        viol_mask = tail_mask & (close_arr > origin_bound)
    viol_positions = np.flatnonzero(viol_mask)
    violation_idx = int(idx_arr[viol_positions[0]]) if len(viol_positions) else None

    for s in relevant:
        if violation_idx is not None and violation_idx <= s.idx:
            return None

        is_higher_low = spike.direction == "bullish" and s.price > last_price
        is_lower_high = spike.direction == "bearish" and s.price < last_price
        if is_higher_low or is_lower_high:
            accepted.append(s)
            last_price = s.price
            if len(accepted) >= config.min_2l_swings:
                break

    if len(accepted) < config.min_2l_swings:
        return None

    last_swing = accepted[-1]
    window = df[(df["idx"] > spike.end_idx) & (df["idx"] <= last_swing.idx)]
    structure_high = float(window["high"].max()) if len(window) else spike.end_price
    structure_low = float(window["low"].min()) if len(window) else spike.end_price

    return Structure2L(
        spike=spike,
        swings=accepted,
        level_price=float(last_swing.price),
        level_time=last_swing.time,
        structure_high=structure_high,
        structure_low=structure_low,
    )


def generate_signal(
    df: pd.DataFrame,
    structure: Structure2L,
    config: SP2LConfig,
    open_arr: np.ndarray,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    close_arr: np.ndarray,
    context_arr: np.ndarray,
) -> Signal | None:
    """Scans forward bar by bar for a retest/breakout trigger. Reads
    pre-extracted numpy arrays instead of `df.iloc[j]` -- row access on a
    mixed-dtype DataFrame forces a per-call dtype-reconciliation pass across
    every column (pandas `fast_xs`), which dominated runtime on real data
    once A1-A4 removed the other per-bar loops. Same sequential-scan logic
    as before, just cheaper cell reads."""
    spike = structure.spike
    start_idx = structure.swings[-1].idx + 1
    origin_bound = (
        spike.origin_price - config.structure_invalidation_buffer
        if spike.direction == "bullish"
        else spike.origin_price + config.structure_invalidation_buffer
    )

    for j in range(start_idx, len(df)):
        o, h, l, c = open_arr[j], high_arr[j], low_arr[j], close_arr[j]

        if spike.direction == "bullish" and c < origin_bound:
            return None
        if spike.direction == "bearish" and c > origin_bound:
            return None

        entry_price = None
        if config.entry_mode == "retest":
            if spike.direction == "bullish" and l <= structure.level_price:
                if not config.require_rejection_candle or (c > o and c >= structure.level_price):
                    entry_price = structure.level_price
            elif spike.direction == "bearish" and h >= structure.level_price:
                if not config.require_rejection_candle or (c < o and c <= structure.level_price):
                    entry_price = structure.level_price
        else:  # breakout
            if spike.direction == "bullish" and c > structure.structure_high:
                entry_price = float(c)
            elif spike.direction == "bearish" and c < structure.structure_low:
                entry_price = float(c)

        if entry_price is None:
            continue

        ctx = context_arr[j]
        if config.use_context_filter:
            if spike.direction == "bullish" and ctx == "downtrend":
                return None
            if spike.direction == "bearish" and ctx == "uptrend":
                return None

        sl_price = (
            spike.origin_price - config.sl_buffer
            if spike.direction == "bullish"
            else spike.origin_price + config.sl_buffer
        )
        if abs(entry_price - sl_price) <= 0:
            return None

        return Signal(
            direction=spike.direction,
            entry_idx=j,
            entry_time=df["time"].iat[j],
            entry_price=float(entry_price),
            sl_price=float(sl_price),
            meta={
                "entry_mode": config.entry_mode,
                "context": str(ctx),
                "spike_start_time": spike.start_time.isoformat(),
                "spike_end_time": spike.end_time.isoformat(),
                "level_price": structure.level_price,
            },
        )

    return None


def generate_signals(df: pd.DataFrame, config: SP2LConfig) -> list[Signal]:
    if len(df) < max(config.spike_atr_len, config.swing_fractal_width * 2 + 1) + config.spike_lookback:
        return []

    df = df.reset_index(drop=True).copy()
    df["idx"] = df.index

    atr = compute_atr(df, config.spike_atr_len)
    swings = compute_swings(df, config.swing_fractal_width)
    context = compute_context(df, config.context_ema_len)
    spikes = detect_spikes(df, atr, config)

    # extracted once, reused across every generate_signal() call below --
    # see generate_signal's docstring for why this matters
    open_arr = df["open"].to_numpy()
    high_arr = df["high"].to_numpy()
    low_arr = df["low"].to_numpy()
    close_arr = df["close"].to_numpy()
    context_arr = context.to_numpy()

    signals: list[Signal] = []
    for spike in spikes:
        structure = find_2l_structure(df, spike, swings, config)
        if structure is None:
            continue
        signal = generate_signal(df, structure, config, open_arr, high_arr, low_arr, close_arr, context_arr)
        if signal is not None:
            signals.append(signal)

    signals.sort(key=lambda s: s.entry_idx)
    return signals


register_strategy(
    StrategyDefinition(
        id="sp2l",
        name="SP2L (Spike + 2L)",
        description=(
            "Context -> Level -> Spike -> 2L (HL/LH) -> Entry (Retest or Breakout) -> SL. "
            "4 Spike types, P-Gap, and the 2X entry model are not implemented -- their exact "
            "rules aren't reliably documented outside the source course video."
        ),
        config_model=SP2LConfig,
        generate_signals=generate_signals,
    )
)
