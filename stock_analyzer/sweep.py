"""Try several settings and check them on data the tuning never saw.

    python -m stock_analyzer.sweep --tickers NVDA TSLA AMD AAPL
Days are split in half: pick settings on the FIRST half, judge on the SECOND.
"""
from __future__ import annotations

import argparse
import itertools

import numpy as np
import pandas as pd

from .backtest import summarize
from .data import fetch_intraday
from .strategy import Params, simulate_day


def trades_for(days: dict, p: Params):
    return [t for d in days.values() if (t := simulate_day(d, p))]


def buy_hold(days: dict) -> list[float]:
    """Baseline: buy the open, sell the close."""
    return [d["close"].iloc[-1] / d["open"].iloc[0] - 1 for d in days.values()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=["NVDA", "TSLA", "AMD", "AAPL"])
    ap.add_argument("--days", type=int, default=28)
    a = ap.parse_args()

    days: dict = {}
    for tk in a.tickers:
        b = fetch_intraday(tk, a.days)
        for dt, g in b.groupby(b.index.date):
            days[(tk, dt)] = g
    dates = sorted({dt for _, dt in days})
    cut = dates[len(dates) // 2]
    train = {k: v for k, v in days.items() if k[1] < cut}
    test = {k: v for k, v in days.items() if k[1] >= cut}
    print(f"{len(dates)} trading days, split at {cut}: train {len(train)} / test {len(test)} ticker-days")

    bh = lambda d: np.mean(buy_hold(d)) * 100
    print(f"Baseline buy-open/sell-close avg: train {bh(train):+.2f}%  test {bh(test):+.2f}%\n")

    rows = []
    for delay, sl, tr in itertools.product([0, 2, 5, 10], [0.01, 0.015, 0.02], [0.007, 0.01, 0.015]):
        p = Params(entry_delay=delay, stop_loss=sl, trail_drop=tr)
        a_, b_ = summarize(trades_for(train, p)), summarize(trades_for(test, p))
        rows.append({"delay_bars": delay, "stop%": sl * 100, "trail%": tr * 100,
                     "train_avg%": a_.get("avg_ret_%", np.nan), "train_win": a_.get("win_rate", np.nan),
                     "test_avg%": b_.get("avg_ret_%", np.nan), "test_win": b_.get("win_rate", np.nan)})
    df = pd.DataFrame(rows).sort_values("train_avg%", ascending=False)
    print("Top 8 by TRAIN avg return (look at the test columns for the honest number):")
    print(df.head(8).round(3).to_string(index=False))
    print("\nBy entry delay (mean over all stop/trail combos):")
    print(df.groupby("delay_bars")[["train_avg%", "test_avg%"]].mean().round(3).to_string())


if __name__ == "__main__":
    main()
