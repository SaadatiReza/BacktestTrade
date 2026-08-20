from __future__ import annotations

import pandas as pd

from app.schemas import ExecutionConfig
from app.strategy.base import StrategyDefinition
from app.strategy.types import SimulatedTrade, Signal


def _mark_to_market_result(r_multiple: float) -> str:
    """A trade that never hit its SL/TP before the backtest ran out of bars
    (end of data, or max_holding_bars) still has to be scored one way or
    another: mark it to the last known price and call it a win, loss, or
    breakeven from that R, instead of leaving it in permanent limbo."""
    if r_multiple > 0:
        return "win"
    if r_multiple < 0:
        return "loss"
    return "breakeven"


def _resolve_single(df: pd.DataFrame, signal: Signal, execution: ExecutionConfig) -> SimulatedTrade:
    sign = 1 if signal.direction == "bullish" else -1
    risk = abs(signal.entry_price - signal.sl_price)
    tp = signal.entry_price + sign * risk * execution.rr_target
    trade = SimulatedTrade(signal=signal, tp1_price=None, tp2_price=tp)

    end_j = min(signal.entry_idx + execution.max_holding_bars, len(df) - 1)
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
            trade.r_multiple, trade.result = execution.rr_target, "win"
            return trade

    last = df.iloc[end_j]
    unrealized_r = sign * (last["close"] - signal.entry_price) / risk if risk else 0.0
    trade.exit_idx, trade.exit_time, trade.exit_price = end_j, last["time"], float(last["close"])
    trade.r_multiple = float(unrealized_r)
    trade.result = _mark_to_market_result(trade.r_multiple)
    return trade


def _resolve_split(df: pd.DataFrame, signal: Signal, execution: ExecutionConfig) -> SimulatedTrade:
    sign = 1 if signal.direction == "bullish" else -1
    risk = abs(signal.entry_price - signal.sl_price)
    tp1 = signal.entry_price + sign * risk * 1.0
    tp2 = signal.entry_price + sign * risk * execution.rr_target
    trade = SimulatedTrade(signal=signal, tp1_price=tp1, tp2_price=tp2)

    end_j = min(signal.entry_idx + execution.max_holding_bars, len(df) - 1)

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
        trade.r_multiple = float(unrealized_r)
        trade.result = _mark_to_market_result(trade.r_multiple)
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
            trade.r_multiple, trade.result = total_r, "win"
            return trade
        if hit_tp2:
            total_r = 0.5 * 1.0 + 0.5 * execution.rr_target
            trade.exit_idx, trade.exit_time, trade.exit_price = j, bar["time"], tp2
            trade.r_multiple, trade.result = total_r, "win"
            return trade

    last = df.iloc[end_j]
    unrealized_r2 = sign * (last["close"] - signal.entry_price) / risk if risk else 0.0
    total_r = 0.5 * 1.0 + 0.5 * unrealized_r2
    trade.exit_idx, trade.exit_time, trade.exit_price = end_j, last["time"], float(last["close"])
    trade.r_multiple = float(total_r)
    trade.result = _mark_to_market_result(trade.r_multiple)
    return trade


def run_backtest(
    df: pd.DataFrame,
    strategy: StrategyDefinition,
    strategy_config,
    execution: ExecutionConfig,
) -> list[SimulatedTrade]:
    df = df.reset_index(drop=True).copy()
    df["idx"] = df.index

    signals = strategy.generate_signals(df, strategy_config)

    trades: list[SimulatedTrade] = []
    last_exit_idx = -1
    for signal in signals:
        risk = abs(signal.entry_price - signal.sl_price)
        if execution.max_risk_price and risk > execution.max_risk_price:
            continue
        if not execution.allow_concurrent_trades and signal.entry_idx <= last_exit_idx:
            continue
        resolve = _resolve_split if execution.tp_mode == "split" else _resolve_single
        trade = resolve(df, signal, execution)
        trades.append(trade)
        if trade.exit_idx is not None:
            last_exit_idx = trade.exit_idx

    return trades


def compute_stats(trades: list[SimulatedTrade]) -> dict:
    # every trade is scored win/loss/breakeven -- see _mark_to_market_result
    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]
    breakevens = [t for t in trades if t.result == "breakeven"]

    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
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

    total = len(trades)
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "winrate": (len(wins) / total) if total else 0.0,
        "avg_r": (sum(r_values) / len(r_values)) if r_values else 0.0,
        "expectancy_r": (sum(r_values) / len(r_values)) if r_values else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "max_drawdown_r": max_dd,
        "equity_curve_r": equity_curve,
    }
