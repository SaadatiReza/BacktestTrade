"""Regression test for the vectorized rewrite of app/strategy/sp2l.py.

The functions below (`_legacy_*`) are a frozen copy of the pre-vectorization
implementations (the ones that used a plain Python loop per bar). They exist
ONLY as a reference to diff against -- never import or call them from
production code. Every test compares the live `app.strategy.sp2l` functions
(which should now be vectorized) against this frozen reference across many
synthetic datasets and config combinations, asserting EXACT equality field
by field. A silently-different spike/swing/structure detection would
corrupt real backtest results, which is worse than being slow -- so "close
enough" is not an acceptable bar here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.schemas import SP2LConfig
from app.strategy import sp2l
from app.strategy.sp2l import Spike, Structure2L, Swing

# ---------------------------------------------------------------------------
# Frozen legacy reference implementations (pre-vectorization)
# ---------------------------------------------------------------------------


def _legacy_compute_swings(df: pd.DataFrame, width: int) -> list[Swing]:
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


def _legacy_compute_context(df: pd.DataFrame, ema_len: int) -> pd.Series:
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


def _legacy_detect_spikes(df: pd.DataFrame, atr: pd.Series, config: SP2LConfig) -> list[Spike]:
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
        i = i + config.spike_lookback
    return spikes


def _legacy_find_2l_structure(
    df: pd.DataFrame, spike: Spike, swings: list[Swing], config: SP2LConfig
) -> Structure2L | None:
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


# ---------------------------------------------------------------------------
# Synthetic OHLC generators
# ---------------------------------------------------------------------------


def _base_ohlc(n: int, seed: int, volatility: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 2000.0
    opens, highs, lows, closes = [], [], [], []
    for _ in range(n):
        o = price
        c = o + rng.normal(0, volatility)
        h = max(o, c) + abs(rng.normal(0, volatility * 0.5))
        l = min(o, c) - abs(rng.normal(0, volatility * 0.5))
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        price = c
    times = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({"time": times, "open": opens, "high": highs, "low": lows, "close": closes})


def _inject_spikes(df: pd.DataFrame, seed: int, every: int = 50) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1)
    df = df.copy()
    for i in range(every, len(df), every):
        direction = 1 if rng.random() > 0.5 else -1
        move = direction * rng.uniform(8, 15)
        df.loc[i, "close"] = df.loc[i, "open"] + move
        df.loc[i, "high"] = max(df.loc[i, "open"], df.loc[i, "close"]) + rng.uniform(0, 0.5)
        df.loc[i, "low"] = min(df.loc[i, "open"], df.loc[i, "close"]) - rng.uniform(0, 0.3)
        if i + 1 < len(df):
            df.loc[i + 1, "open"] = df.loc[i, "close"]
    return df


def _inject_zero_range_bars(df: pd.DataFrame, seed: int, every: int = 30) -> pd.DataFrame:
    df = df.copy()
    for i in range(every, len(df), every):
        flat = df.loc[i, "open"]
        df.loc[i, "high"] = flat
        df.loc[i, "low"] = flat
        df.loc[i, "close"] = flat
    return df


def _inject_flat_plateau(df: pd.DataFrame, seed: int, at: int, width: int = 5) -> pd.DataFrame:
    df = df.copy()
    if at + width >= len(df):
        return df
    peak = df["high"].max() + 5
    trough = df["low"].min() - 5
    for k in range(width):
        df.loc[at + k, "high"] = peak
        df.loc[at + k, "low"] = trough
    return df


def synthetic_datasets():
    cases = []
    for n in (60, 300, 2000):
        for seed in (1, 2, 3):
            df = _base_ohlc(n, seed, volatility=1.2)
            df = _inject_spikes(df, seed)
            cases.append((f"n{n}_seed{seed}_spikes", df))

    df_zero = _base_ohlc(500, 10, volatility=1.0)
    df_zero = _inject_spikes(df_zero, 10)
    df_zero = _inject_zero_range_bars(df_zero, 10)
    cases.append(("zero_range_bars", df_zero))

    df_plateau = _base_ohlc(500, 20, volatility=1.0)
    df_plateau = _inject_spikes(df_plateau, 20)
    df_plateau = _inject_flat_plateau(df_plateau, 20, at=250, width=5)
    cases.append(("flat_plateau", df_plateau))

    return cases


def config_sweep():
    configs = []
    for spike_lookback in (1, 3, 10):
        for spike_atr_len in (2, 14):
            for swing_fractal_width in (1, 2, 5):
                for min_2l_swings in (1, 2, 5):
                    for entry_mode in ("retest", "breakout"):
                        configs.append(
                            SP2LConfig(
                                spike_lookback=spike_lookback,
                                spike_atr_len=spike_atr_len,
                                swing_fractal_width=swing_fractal_width,
                                min_2l_swings=min_2l_swings,
                                entry_mode=entry_mode,
                            )
                        )
    return configs


# a smaller, representative subset for the O(datasets x configs) full pipeline check
def config_sweep_small():
    return [
        SP2LConfig(entry_mode="retest"),
        SP2LConfig(entry_mode="breakout"),
        SP2LConfig(spike_lookback=1, spike_atr_len=2),
        SP2LConfig(spike_lookback=10, spike_atr_len=2),
        SP2LConfig(swing_fractal_width=1, min_2l_swings=1),
        SP2LConfig(swing_fractal_width=5, min_2l_swings=5),
    ]


def _swing_tuple(s: Swing) -> tuple:
    return (s.idx, s.time, s.price, s.kind)


def _spike_tuple(sp: Spike) -> tuple:
    return (sp.direction, sp.start_idx, sp.end_idx, sp.start_time, sp.end_time, sp.origin_price, sp.end_price)


def _structure_tuple(st: Structure2L | None) -> tuple | None:
    if st is None:
        return None
    return (
        _spike_tuple(st.spike),
        tuple(_swing_tuple(s) for s in st.swings),
        st.level_price,
        st.level_time,
        st.structure_high,
        st.structure_low,
    )


def _signal_tuple(sig) -> tuple | None:
    if sig is None:
        return None
    return (
        sig.direction,
        sig.entry_idx,
        sig.entry_time,
        sig.entry_price,
        sig.sl_price,
        tuple(sorted(sig.meta.items())),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,df", synthetic_datasets())
@pytest.mark.parametrize("width", [1, 2, 5])
def test_compute_swings_matches_legacy(name, df, width):
    legacy = [_swing_tuple(s) for s in _legacy_compute_swings(df, width)]
    current = [_swing_tuple(s) for s in sp2l.compute_swings(df, width)]
    assert current == legacy, f"compute_swings mismatch for {name}, width={width}"


@pytest.mark.parametrize("name,df", synthetic_datasets())
@pytest.mark.parametrize("ema_len", [5, 50])
def test_compute_context_matches_legacy(name, df, ema_len):
    legacy = _legacy_compute_context(df, ema_len).tolist()
    current = sp2l.compute_context(df, ema_len).tolist()
    assert current == legacy, f"compute_context mismatch for {name}, ema_len={ema_len}"


@pytest.mark.parametrize("name,df", synthetic_datasets())
@pytest.mark.parametrize("config", config_sweep())
def test_detect_spikes_matches_legacy(name, df, config):
    df = df.reset_index(drop=True).copy()
    df["idx"] = df.index
    atr = sp2l.compute_atr(df, config.spike_atr_len)
    legacy = [_spike_tuple(s) for s in _legacy_detect_spikes(df, atr, config)]
    current = [_spike_tuple(s) for s in sp2l.detect_spikes(df, atr, config)]
    assert current == legacy, f"detect_spikes mismatch for {name}, config={config}"


@pytest.mark.parametrize("name,df", synthetic_datasets())
@pytest.mark.parametrize("config", config_sweep())
def test_find_2l_structure_matches_legacy(name, df, config):
    df = df.reset_index(drop=True).copy()
    df["idx"] = df.index
    atr = sp2l.compute_atr(df, config.spike_atr_len)
    swings = sp2l.compute_swings(df, config.swing_fractal_width)
    spikes = sp2l.detect_spikes(df, atr, config)

    for spike in spikes:
        legacy = _structure_tuple(_legacy_find_2l_structure(df, spike, swings, config))
        current = _structure_tuple(sp2l.find_2l_structure(df, spike, swings, config))
        assert current == legacy, f"find_2l_structure mismatch for {name}, spike={spike}, config={config}"


@pytest.mark.parametrize("name,df", synthetic_datasets())
@pytest.mark.parametrize("config", config_sweep_small())
def test_generate_signals_end_to_end(name, df, config):
    """Full pipeline sanity check -- not a legacy diff (generate_signals
    itself wasn't rewritten), but confirms the vectorized building blocks
    compose correctly into working signals with no exceptions and sane
    invariants (SL/entry never equal, entry_idx within bounds)."""
    signals = sp2l.generate_signals(df, config)
    for sig in signals:
        assert 0 <= sig.entry_idx < len(df)
        assert sig.sl_price != sig.entry_price
        if sig.direction == "bullish":
            assert sig.sl_price < sig.entry_price
        else:
            assert sig.sl_price > sig.entry_price
