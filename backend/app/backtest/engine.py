from __future__ import annotations

import pandas as pd

from app.schemas import SP2LConfig
from app.strategy.sp2l import generate_signals
from app.strategy.types import SimulatedTrade, Signal


def _resolve_single(df: pd.DataFrame, signal: Signal, config: SP2LConfig) -> SimulatedTrade:
    trade = SimulatedTrade(signal=signal)
    sign = 1 if signal.direction == "bullish" else -1
    tp = signal.tp2_price
    risk = abs(signal.entry_price - signal.sl_price)

    end_j = min(signal.entry_idx + config.max_holding_bars, len(df) - 1)
    for j in range(signal.entry_idx + 1, end_j + 1):
        bar = df.iloc[j]
        hit_sl = bar["low"] <= signal.sl_price if sign == 1 else bar["high"] >= signal.sl_price
        hit_tp = bar["high"] >= tp if sign == 1 else bar["low"] <= tp
        # conservative convention: if both levels fall inside the same bar's range,
        # assume the stop is hit first
        if hit_sl:
            trade.exit_idx, trade.exit_time, trade.exit_price = j, bar["time"], signal.sl_price
            trade.r_multiple, trade.result = -1.0, "loss"
            return trade
        if hit_tp:
            trade.exit_idx, trade.exit_time, trade.exit_price = j, bar["time"], tp
            trade.r_multiple, trade.result = config.rr_target, "win"
            return trade

    last = df.iloc[end_j]
    unrealized_r = sign * (last["close"] - signal.entry_price) / risk if risk else 0.0
    trade.exit_idx, trade.exit_time, trade.exit_price = end_j, last["time"], float(last["close"])
    trade.r_multiple, trade.result = float(unrealized_r), "open"
    return trade


def _resolve_split(df: pd.DataFrame, signal: Signal, config: SP2LConfig) -> SimulatedTrade:
    trade = SimulatedTrade(signal=signal)
    sign = 1 if signal.direction == "bullish" else -1
    risk = abs(signal.entry_price - signal.sl_price)
    tp1, tp2 = signal.tp1_price, signal.tp2_price

    end_j = min(signal.entry_idx + config.max_holding_bars, len(df) - 1)

    # phase 1: waiting for SL or TP1
    tp1_hit_at = None
    for j in range(signal.entry_idx + 1, end_j + 1):
        bar = df.iloc[j]
        hit_sl = bar["low"] <= signal.sl_price if sign == 1 else bar["high"] >= signal.sl_price
        hit_tp1 = bar["high"] >= tp1 if sign == 1 else bar["low"] <= tp1
        if hit_sl:
            trade.exit_idx, trade.exit_time, trade.exit_price = j, bar["time"], signal.sl_price
            trade.r_multiple, trade.result = -1.0, "loss"
            return trade
        if hit_tp1:
            tp1_hit_at = j
            break

    if tp1_hit_at is None:
        last = df.iloc[end_j]
        unrealized_r = sign * (last["close"] - signal.entry_price) / risk if risk else 0.0
        trade.exit_idx, trade.exit_time, trade.exit_price = end_j, last["time"], float(last["close"])
        trade.r_multiple, trade.result = float(unrealized_r), "open"
        return trade

    # phase 2: half position runs with SL moved to breakeven, target TP2
    breakeven = signal.entry_price
    for j in range(tp1_hit_at + 1, end_j + 1):
        bar = df.iloc[j]
        hit_be = bar["low"] <= breakeven if sign == 1 else bar["high"] >= breakeven
        hit_tp2 = bar["high"] >= tp2 if sign == 1 else bar["low"] <= tp2
        if hit_be:
            total_r = 0.5 * 1.0 + 0.5 * 0.0
            trade.exit_idx, trade.exit_time, trade.exit_price = j, bar["time"], breakeven
            trade.r_multiple, trade.result = total_r, ("breakeven" if total_r == 0 else "win")
            return trade
        if hit_tp2:
            total_r = 0.5 * 1.0 + 0.5 * config.rr_target
            trade.exit_idx, trade.exit_time, trade.exit_price = j, bar["time"], tp2
            trade.r_multiple, trade.result = total_r, "win"
            return trade

    last = df.iloc[end_j]
    unrealized_r2 = sign * (last["close"] - signal.entry_price) / risk if risk else 0.0
    total_r = 0.5 * 1.0 + 0.5 * unrealized_r2
    trade.exit_idx, trade.exit_time, trade.exit_price = end_j, last["time"], float(last["close"])
    trade.r_multiple, trade.result = float(total_r), "open"
    return trade


def run_backtest(df: pd.DataFrame, config: SP2LConfig) -> list[SimulatedTrade]:
    df = df.reset_index(drop=True).copy()
    df["idx"] = df.index

    signals = generate_signals(df, config)

    trades: list[SimulatedTrade] = []
    last_exit_idx = -1
    for signal in signals:
        if not config.allow_concurrent_trades and signal.entry_idx <= last_exit_idx:
            continue
        resolve = _resolve_split if config.tp_mode == "split" else _resolve_single
        trade = resolve(df, signal, config)
        trades.append(trade)
        if trade.exit_idx is not None:
            last_exit_idx = trade.exit_idx

    return trades


def compute_stats(trades: list[SimulatedTrade]) -> dict:
    closed = [t for t in trades if t.result in ("win", "loss", "breakeven")]
    wins = [t for t in closed if t.result == "win"]
    losses = [t for t in closed if t.result == "loss"]
    breakevens = [t for t in closed if t.result == "breakeven"]
    opens = [t for t in trades if t.result == "open"]

    r_values = [t.r_multiple for t in closed if t.r_multiple is not None]
    equity_curve: list[float] = []
    running = 0.0
    for r in r_values:
        running += r
        equity_curve.append(running)

    gross_win = sum(r for r in r_values if r > 0)
    gross_loss = abs(sum(r for r in r_values if r < 0))

    peak = float("-inf")
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)

    total_closed = len(closed)
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "open_trades": len(opens),
        "winrate": (len(wins) / total_closed) if total_closed else 0.0,
        "avg_r": (sum(r_values) / len(r_values)) if r_values else 0.0,
        "expectancy_r": (sum(r_values) / len(r_values)) if r_values else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "max_drawdown_r": max_dd,
        "equity_curve_r": equity_curve,
    }
