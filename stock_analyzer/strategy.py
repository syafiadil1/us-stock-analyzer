"""Intraday strategy: buy at the open, watch every 3 minutes, sell by rules.

Rules (from the trader's own routine):
  * BUY at the market open (09:30 New York time).
  * Check the % change vs the entry price at every 3-minute bar close.
  * SELL at once when up >= take_profit.
  * SELL when price is whipsawing (many fast up/down swings)      -> CHOPPY
  * HOLD while price keeps grinding up, but SELL when it suddenly
    drops a lot from its running high                             -> TRAILING_DROP
  * GIVEBACK (optional): once the trade is up at least giveback_arm, SELL when
    it has handed back `giveback` of its best gain. Example giveback=0.6:
    entry 100, peak 103 (+3%) -> sell at 101.20 (kept 40% of the gain).
  * Extra safety (not in the original routine, defaults can be tuned):
    hard STOP_LOSS and forced sale at the close (no overnight risk).

Entry modes:
  * entry="open"      buy `entry_delay` bars after the open (the original routine).
  * entry="breakout"  only buy if the market (QQQ) is above its open after ~10 min,
                      and the stock closes above its opening-range high (the high of the
                      bars that start before `or_end`) no later than `last_entry`.
                      Buy at the next bar's open. In a 60-day test on ~100 stocks this
                      raised the win rate from ~48% to ~73% (see README).
                      max_gap: also skip stocks that opened more than max_gap above
                      yesterday's close (big gap-ups tended to fade in 2 years of data).

Decisions use bar CLOSES only (like glancing at the chart every 3 minutes),
which is a conservative simplification: intrabar spikes are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd


@dataclass(frozen=True)
class Params:
    take_profit: float = 0.02      # sell when up 2% (0 = off, let winners run)
    stop_loss: float = 0.01        # hard stop: down 1% from entry
    trail_drop: float = 0.007      # sell if price falls 0.7% from running high (0 = off)
    giveback: float = 0.0          # sell after handing back this share of the best gain (0 = off)
    giveback_arm: float = 0.005    # ...but only once the trade has been up at least 0.5%
    chop_window: int = 8           # look back this many 3-min bars (~24 min)
    chop_move: float = 0.0015      # a bar-to-bar move counts as a "swing" if >= 0.15%
    chop_flips: int = 4            # this many direction flips inside the window = choppy
    slippage: float = 0.0005       # 0.05% worse fill on each side
    entry_delay: int = 0           # wait this many 3-min bars after the open before buying
    entry: str = "open"            # "open" or "breakout" (see module docstring)
    or_end: str = "10:15"          # breakout: opening range = bars starting before this time
    last_entry: str = "11:00"      # breakout: no new entries for bars starting after this
    market_filter: bool = True     # breakout: require QQQ above its open...
    market_check: str = "09:39"    # ...using the bars that start before this time
    max_gap: float = 0.0           # breakout: skip if it opened > this above yesterday's close (0 = off)


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
    """True when the last `chop_window` bars show many fast direction flips (chop_flips=0: off)."""
    if p.chop_flips <= 0 or len(closes) < p.chop_window + 1:
        return False
    window = closes[-(p.chop_window + 1):]
    moves = [b / a - 1 for a, b in zip(window[:-1], window[1:])]
    big = [m for m in moves if abs(m) >= p.chop_move]
    flips = sum(1 for a, b in zip(big[:-1], big[1:]) if (a > 0) != (b > 0))
    return flips >= p.chop_flips


def giveback_price(entry: float, peak: float, p: Params) -> float | None:
    """Price at which the GIVEBACK rule sells, or None while it is not armed."""
    if p.giveback <= 0 or peak / entry - 1 < p.giveback_arm:
        return None
    return entry + (peak - entry) * (1 - p.giveback)


def _hhmm(ts) -> str:
    return pd.Timestamp(ts).strftime("%H:%M")


def _before(bars: pd.DataFrame, hhmm: str) -> pd.DataFrame:
    return bars[[_hhmm(t) < hhmm for t in bars.index]]


def market_ok(index_bars: pd.DataFrame | None, p: Params) -> bool | None:
    """Is the market index (QQQ) above its open after the first bars? None = not known yet
    (the check is known once a bar starting at/after market_check exists)."""
    if index_bars is None or index_bars.empty or _hhmm(index_bars.index[-1]) < p.market_check:
        return None
    early = _before(index_bars, p.market_check)
    if early.empty:
        return None
    return bool(early["close"].iloc[-1] > early["open"].iloc[0])


def opening_range_high(bars: pd.DataFrame, p: Params) -> float | None:
    """High of the opening range, or None while it is still forming."""
    if bars.empty or _hhmm(bars.index[-1]) < p.or_end:
        return None
    rng = _before(bars, p.or_end)
    return None if rng.empty else float(rng["high"].max())


def breakout_entry(bars: pd.DataFrame, p: Params) -> int | None:
    """Index of the entry bar (= the bar AFTER the first close above the opening-range
    high), or None if there is no breakout (yet). May equal len(bars) in live use:
    the breakout bar just closed and the entry is "now"."""
    hi = opening_range_high(bars, p)
    if hi is None:
        return None
    for i, (ts, c) in enumerate(zip(bars.index, bars["close"])):
        t = _hhmm(ts)
        if t < p.or_end:
            continue
        if t > p.last_entry:
            return None
        if c > hi:
            return i + 1
    return None


def gap_too_big(bars: pd.DataFrame, prev_close: float | None, p: Params) -> bool:
    """True when the max_gap rule says skip (unknown prev_close = do not skip)."""
    if p.max_gap <= 0 or not prev_close or bars.empty:
        return False
    return float(bars["open"].iloc[0]) / prev_close - 1 > p.max_gap


def simulate_session(bars: pd.DataFrame, p: Params = Params(), index_bars: pd.DataFrame | None = None,
                     final: bool = True, prev_close: float | None = None) -> Trade | None:
    """One trading day with either entry mode. None = no trade (or still holding when final=False)."""
    if p.entry == "open":
        return simulate_day(bars, p, final=final)
    if p.market_filter and not market_ok(index_bars, p):
        return None
    if gap_too_big(bars, prev_close, p):
        return None
    k = breakout_entry(bars, p)
    if k is None or k >= len(bars):
        return None
    return simulate_day(bars.iloc[k:], replace(p, entry_delay=0), final=final)


def simulate_day(bars: pd.DataFrame, p: Params = Params(), final: bool = True) -> Trade | None:
    """Run one trading day. `bars` = 3-minute OHLC for a single session.

    final=False is for live use: the day is still running, so the last bar is
    NOT forced out at END_OF_DAY (returns None = keep holding)."""
    bars = bars.iloc[p.entry_delay:]
    if len(bars) < 1:
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
        gb = giveback_price(entry, peak, p)
        if p.take_profit > 0 and pct >= p.take_profit:
            reason = "TAKE_PROFIT"
        elif pct <= -p.stop_loss:
            reason = "STOP_LOSS"
        elif gb is not None and c <= gb:
            reason = "GIVEBACK"
        elif p.trail_drop > 0 and peak > entry and c / peak - 1 <= -p.trail_drop:
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


# Settings that held up best in the 60-day, ~100-stock test (README "Recommended setup").
# slippage 0.1%/side is deliberately pessimistic for these fast, cheaper stocks.
RECOMMENDED = Params(entry="breakout", or_end="10:15", last_entry="11:00", market_filter=True,
                     max_gap=0.03, stop_loss=0.03, take_profit=0.0, trail_drop=0.0, giveback=0.4,
                     giveback_arm=0.005, chop_flips=0, entry_delay=0, slippage=0.001)
