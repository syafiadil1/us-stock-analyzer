"""Backtest the open-buy strategy over many tickers and days.

    python -m stock_analyzer.backtest --tickers NVDA TSLA AMD --days 28
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .data import fetch_intraday
from .strategy import Params, Trade, simulate_day


def run_ticker(bars3: pd.DataFrame, p: Params) -> list[Trade]:
    trades = []
    for _, day in bars3.groupby(bars3.index.date):
        t = simulate_day(day, p)
        if t:
            trades.append(t)
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t.ret for t in trades])
    equity = np.cumprod(1 + r)
    dd = (equity / np.maximum.accumulate(equity) - 1).min()
    reasons = pd.Series([t.reason for t in trades]).value_counts().to_dict()
    return {
        "trades": len(r),
        "win_rate": float((r > 0).mean()),
        "avg_ret_%": float(r.mean() * 100),
        "median_ret_%": float(np.median(r) * 100),
        "best_%": float(r.max() * 100),
        "worst_%": float(r.min() * 100),
        "compounded_%": float((equity[-1] - 1) * 100),
        "max_drawdown_%": float(dd * 100),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        "exit_reasons": reasons,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=["NVDA", "TSLA", "AMD", "AAPL"])
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--take-profit", type=float, default=Params.take_profit)
    ap.add_argument("--stop-loss", type=float, default=Params.stop_loss)
    ap.add_argument("--trail-drop", type=float, default=Params.trail_drop)
    a = ap.parse_args()
    p = Params(take_profit=a.take_profit, stop_loss=a.stop_loss, trail_drop=a.trail_drop)

    all_trades: list[Trade] = []
    for tk in a.tickers:
        try:
            trades = run_ticker(fetch_intraday(tk, a.days), p)
        except Exception as e:  # keep going if one ticker fails
            print(f"{tk}: skipped ({e})")
            continue
        all_trades += trades
        s = summarize(trades)
        print(f"\n== {tk} ==")
        for k, v in s.items():
            print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")

    print("\n== ALL TICKERS ==")
    for k, v in summarize(all_trades).items():
        print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")
    print("\nNOTE: ~1 month of data is a SMALL sample. Past results do not predict the future.")


if __name__ == "__main__":
    main()
