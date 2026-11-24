"""Live paper-trading monitor (alerts only, no orders, no API key).

    python -m stock_analyzer.monitor NVDA TSLA AMD
    python -m stock_analyzer.monitor NVDA --replay 2026-11-20      # replay a past day

Starts at the open, re-checks every 3 minutes with the SAME rules as the
backtest, prints BUY / HOLD / SELL and writes closed paper trades to
paper_trades.csv. Yahoo's free feed can lag, so treat prices as approximate.
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import pandas as pd

from .data import NY, to_3min
from .strategy import Params, simulate_day

JOURNAL = Path("paper_trades.csv")


def decide(bars: pd.DataFrame, p: Params, final: bool = False) -> tuple[str, str]:
    """Return (action, detail) for a day's 3-min bars so far."""
    day = bars.iloc[p.entry_delay:]
    if len(day) < 1:
        return "WAIT", f"waiting for entry (delay {p.entry_delay} bars)"
    entry = day["open"].iloc[0] * (1 + p.slippage)
    t = simulate_day(bars, p, final=final)
    if t is not None:
        return "SELL", f"{t.reason} {t.ret * 100:+.2f}% (entry {t.entry:.2f} exit {t.exit:.2f})"
    if len(day) == 1:
        return "BUY", (f"enter ~{entry:.2f} | take profit {entry * (1 + p.take_profit):.2f} "
                       f"| stop {entry * (1 - p.stop_loss):.2f}")
    pct = (day["close"].iloc[-1] / entry - 1) * 100
    return "HOLD", f"{pct:+.2f}% vs entry"


def log_trade(ticker: str, bars: pd.DataFrame, p: Params, detail: str) -> None:
    new = not JOURNAL.exists()
    with JOURNAL.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "ticker", "result"])
        w.writerow([bars.index[-1].date(), ticker, detail])


def fetch_today(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(ticker, period="1d", interval="1m", progress=False,
                      auto_adjust=True, multi_level_index=False)
    if raw.empty:
        return raw
    b = to_3min(raw)
    return b[b.index.date == b.index[-1].date()]


def run_live(tickers: list[str], p: Params) -> None:
    done: set[str] = set()
    bought: set[str] = set()
    while len(done) < len(tickers):
        now = pd.Timestamp.now(tz=NY)
        if now.weekday() >= 5 or now.time() < pd.Timestamp("09:30").time():
            print(f"{now:%a %H:%M} NY: market not open yet; waiting...")
            time.sleep(60)
            continue
        final = now.time() >= pd.Timestamp("15:57").time()
        for tk in tickers:
            if tk in done:
                continue
            bars = fetch_today(tk)
            if bars.empty:
                continue
            action, detail = decide(bars, p, final=final)
            if action == "BUY":
                if tk in bought:  # already announced; same entry bar seen twice
                    action, detail = "HOLD", "in position"
                bought.add(tk)
            print(f"{now:%H:%M} {tk:6s} {action:5s} {detail}")
            if action == "SELL":
                log_trade(tk, bars, p, detail)
                done.add(tk)
        if final:
            break
        time.sleep(180 - (time.time() % 180))  # wake on the next 3-minute mark


def replay(ticker: str, day: str, p: Params) -> None:
    from .data import fetch_intraday

    all_bars = fetch_intraday(ticker)
    bars = all_bars[all_bars.index.date == pd.Timestamp(day).date()]
    if bars.empty:
        raise SystemExit(f"No data for {ticker} on {day}")
    for n in range(1, len(bars) + 1):
        action, detail = decide(bars.iloc[:n], p, final=(n == len(bars)))
        print(f"{bars.index[n - 1]:%H:%M} {ticker} {action:5s} {detail}")
        if action == "SELL":
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--replay", help="YYYY-MM-DD: replay that day instead of going live")
    ap.add_argument("--delay", type=int, default=2, help="3-min bars to wait after the open")
    ap.add_argument("--stop-loss", type=float, default=0.015)
    ap.add_argument("--trail-drop", type=float, default=0.015)
    a = ap.parse_args()
    params = Params(entry_delay=a.delay, stop_loss=a.stop_loss, trail_drop=a.trail_drop)
    if a.replay:
        replay(a.tickers[0], a.replay, params)
    else:
        run_live(a.tickers, params)
