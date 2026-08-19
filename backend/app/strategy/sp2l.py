"""SP2L strategy detection: Context -> Level -> Spike -> 2L -> Entry -> SL -> TP.

This module implements ONLY the parts of SP2L that are corroborated across
the course's public outline and general SP2L write-ups:
  - Spike detection (a fast, imbalanced directional move)
  - 2L structure: a sequence of Higher-Lows (bullish) / Lower-Highs (bearish)
    following the spike, each acting as a potential Level
  - Context filter (higher-timeframe trend alignment)
  - Entry via Retest of a Level, or Breakout of structure (pick one per run)
  - SL beyond the spike origin, TP via R:R (single or split TP1/TP2)

Deliberately NOT implemented (see backend/README.md): the 4 Spike sub-types,
P-Gap, and the 2X entry model. These are separate, dedicated sections in the
course outline whose exact rules are not reliably documented in public
sources. Rather than guess and silently bake in a wrong rule, these are left
as explicit gaps to fill in once verified against the source video.
"""

from __future__ import annotations

import pandas as pd

from app.schemas import SP2LConfig
from app.strategy.types import Signal, Spike, Structure2L, Swing


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
    swings: list[Swing] = []
    n = len(df)
    for i in range(width, n - width):
        window_high = df["high"].iloc[i - width : i + width + 1]
        window_low = df["low"].iloc[i - width : i + width + 1]
        if df["high"].iloc[i] == window_high.max():
            swings.append(Swing(idx=i, time=df["time"].iloc[i], price=df["high"].iloc[i], kind="high"))
        if df["low"].iloc[i] == window_low.min():
            swings.append(Swing(idx=i, time=df["time"].iloc[i], price=df["low"].iloc[i], kind="low"))
    swings.sort(key=lambda s: s.idx)
    return swings


def compute_context(df: pd.DataFrame, ema_len: int) -> pd.Series:
    ema = df["close"].ewm(span=ema_len, adjust=False).mean()
    slope = ema.diff(5)

    def classify(i: int) -> str:
        if pd.isna(ema.iloc[i]) or pd.isna(slope.iloc[i]):
            return "range"
        if df["close"].iloc[i] > ema.iloc[i] and slope.iloc[i] > 0:
            return "uptrend"
        if df["close"].iloc[i] < ema.iloc[i] and slope.iloc[i] < 0:
            return "downtrend"
        return "range"

    return pd.Series([classify(i) for i in range(len(df))], index=df.index)


def detect_spikes(df: pd.DataFrame, atr: pd.Series, config: SP2LConfig) -> list[Spike]:
    spikes: list[Spike] = []
    n = len(df)
    i = config.spike_atr_len
    while i < n:
        lo = i - config.spike_lookback + 1
        if lo < 0:
            i += 1
            continue
        window = df.iloc[lo : i + 1]
        atr_i = atr.iloc[i]
        if pd.isna(atr_i) or atr_i <= 0:
            i += 1
            continue

        net_move = window["close"].iloc[-1] - window["open"].iloc[0]
        if abs(net_move) < atr_i * config.spike_atr_mult:
            i += 1
            continue

        ranges = (window["high"] - window["low"]).replace(0, pd.NA)
        body_ratios = (window["close"] - window["open"]).abs() / ranges
        avg_body_ratio = body_ratios.mean(skipna=True)
        if pd.isna(avg_body_ratio) or avg_body_ratio < config.min_body_ratio:
            i += 1
            continue

        direction = "bullish" if net_move > 0 else "bearish"
        origin_price = window["low"].min() if direction == "bullish" else window["high"].max()
        spike = Spike(
            direction=direction,
            start_idx=lo,
            end_idx=i,
            start_time=window["time"].iloc[0],
            end_time=window["time"].iloc[-1],
            origin_price=float(origin_price),
            end_price=float(window["close"].iloc[-1]),
        )
        spikes.append(spike)
        i = i + config.spike_lookback  # jump past this move instead of re-detecting it bar by bar
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

    for s in relevant:
        # structure invalidated if price closed beyond the spike origin before we
        # accumulated enough HL/LH swings
        between = df[(df["idx"] > spike.end_idx) & (df["idx"] <= s.idx)]
        if spike.direction == "bullish" and (between["close"] < origin_bound).any():
            return None
        if spike.direction == "bearish" and (between["close"] > origin_bound).any():
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


def generate_signal(df: pd.DataFrame, structure: Structure2L, context: pd.Series, config: SP2LConfig) -> Signal | None:
    spike = structure.spike
    start_idx = structure.swings[-1].idx + 1
    origin_bound = (
        spike.origin_price - config.structure_invalidation_buffer
        if spike.direction == "bullish"
        else spike.origin_price + config.structure_invalidation_buffer
    )

    for j in range(start_idx, len(df)):
        bar = df.iloc[j]

        if spike.direction == "bullish" and bar["close"] < origin_bound:
            return None
        if spike.direction == "bearish" and bar["close"] > origin_bound:
            return None

        entry_price = None
        if config.entry_mode == "retest":
            if spike.direction == "bullish" and bar["low"] <= structure.level_price:
                if not config.require_rejection_candle or (bar["close"] > bar["open"] and bar["close"] >= structure.level_price):
                    entry_price = structure.level_price
            elif spike.direction == "bearish" and bar["high"] >= structure.level_price:
                if not config.require_rejection_candle or (bar["close"] < bar["open"] and bar["close"] <= structure.level_price):
                    entry_price = structure.level_price
        else:  # breakout
            if spike.direction == "bullish" and bar["close"] > structure.structure_high:
                entry_price = float(bar["close"])
            elif spike.direction == "bearish" and bar["close"] < structure.structure_low:
                entry_price = float(bar["close"])

        if entry_price is None:
            continue

        if config.use_context_filter:
            ctx = context.iloc[j]
            if spike.direction == "bullish" and ctx == "downtrend":
                return None
            if spike.direction == "bearish" and ctx == "uptrend":
                return None
        else:
            ctx = context.iloc[j]

        sl_price = (
            spike.origin_price - config.sl_buffer
            if spike.direction == "bullish"
            else spike.origin_price + config.sl_buffer
        )
        risk = abs(entry_price - sl_price)
        if risk <= 0:
            return None
        if config.max_risk_price and risk > config.max_risk_price:
            return None

        sign = 1 if spike.direction == "bullish" else -1
        if config.tp_mode == "split":
            tp1 = entry_price + sign * risk * 1.0
            tp2 = entry_price + sign * risk * config.rr_target
        else:
            tp1 = None
            tp2 = entry_price + sign * risk * config.rr_target

        return Signal(
            direction=spike.direction,
            entry_mode=config.entry_mode,
            context=str(ctx),
            structure=structure,
            entry_idx=j,
            entry_time=bar["time"],
            entry_price=float(entry_price),
            sl_price=float(sl_price),
            tp1_price=float(tp1) if tp1 is not None else None,
            tp2_price=float(tp2) if tp2 is not None else None,
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

    signals: list[Signal] = []
    for spike in spikes:
        structure = find_2l_structure(df, spike, swings, config)
        if structure is None:
            continue
        signal = generate_signal(df, structure, context, config)
        if signal is not None:
            signals.append(signal)

    signals.sort(key=lambda s: s.entry_idx)
    return signals
