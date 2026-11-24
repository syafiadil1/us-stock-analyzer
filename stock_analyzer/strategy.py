"""Intraday strategy: buy at the open, watch every 3 minutes, sell by rules.

Rules (from the trader's own routine):
  * BUY at the market open (09:30 New York time).
  * Check the % change vs the entry price at every 3-minute bar close.
  * SELL at once when up >= take_profit.
  * SELL when price is whipsawing (many fast up/down swings)      -> CHOPPY
  * HOLD while price keeps grinding up, but SELL when it suddenly
    drops a lot from its running high                             -> TRAILING_DROP
  * Extra safety (not in the original routine, defaults can be tuned):
    hard STOP_LOSS and forced sale at the close (no overnight risk).

Decisions use bar CLOSES only (like glancing at the chart every 3 minutes),
which is a conservative simplification: intrabar spikes are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Params:
    take_profit: float = 0.02      # sell when up 2%
    stop_loss: float = 0.01        # hard stop: down 1% from entry
    trail_drop: float = 0.007      # sell if price falls 0.7% from running high
    chop_window: int = 8           # look back this many 3-min bars (~24 min)
    chop_move: float = 0.0015      # a bar-to-bar move counts as a "swing" if >= 0.15%
    chop_flips: int = 4            # this many direction flips inside the window = choppy
    slippage: float = 0.0005       # 0.05% worse fill on each side
    entry_delay: int = 0           # wait this many 3-min bars after the open before buying


@dataclass(frozen=True)
class Trade:
    date: pd.Timestamp
    entry: float
    exit: float
    exit_time: pd.Timestamp
    reason: str
    ret: float          # net return after slippage, e.g. 0.012 = +1.2%
    bars_held: int
    max_run_up: float   # best unrealised % reached during the trade


def _is_choppy(closes: list[float], p: Params) -> bool:
    """True when the last `chop_window` bars show many fast direction flips."""
    if len(closes) < p.chop_window + 1:
        return False
    window = closes[-(p.chop_window + 1):]
    moves = [b / a - 1 for a, b in zip(window[:-1], window[1:])]
    big = [m for m in moves if abs(m) >= p.chop_move]
    flips = sum(1 for a, b in zip(big[:-1], big[1:]) if (a > 0) != (b > 0))
    return flips >= p.chop_flips


def simulate_day(bars: pd.DataFrame, p: Params = Params(), final: bool = True) -> Trade | None:
    """Run one trading day. `bars` = 3-minute OHLC for a single session.

    final=False is for live use: the day is still running, so the last bar is
    NOT forced out at END_OF_DAY (returns None = keep holding)."""
    bars = bars.iloc[p.entry_delay:]
    if len(bars) < 2:
        return None
    entry = float(bars["open"].iloc[0]) * (1 + p.slippage)
    peak = entry
    closes: list[float] = [float(bars["open"].iloc[0])]
    max_run_up = 0.0
    last = len(bars) - 1

    for i, (ts, row) in enumerate(bars.iterrows()):
        c = float(row["close"])
        closes.append(c)
        pct = c / entry - 1
        peak = max(peak, c)
        max_run_up = max(max_run_up, pct)

        reason = None
        if pct >= p.take_profit:
            reason = "TAKE_PROFIT"
        elif pct <= -p.stop_loss:
            reason = "STOP_LOSS"
        elif peak > entry and c / peak - 1 <= -p.trail_drop:
            reason = "TRAILING_DROP"
        elif _is_choppy(closes, p):
            reason = "CHOPPY"
        elif i == last and final:
            reason = "END_OF_DAY"

        if reason:
            exit_px = c * (1 - p.slippage)
            return Trade(
                date=pd.Timestamp(ts).normalize(),
                entry=entry,
                exit=exit_px,
                exit_time=ts,
                reason=reason,
                ret=exit_px / entry - 1,
                bars_held=i + 1,
                max_run_up=max_run_up,
            )
    return None
