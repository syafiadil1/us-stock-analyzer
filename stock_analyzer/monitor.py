"""Live paper-trading monitor (alerts only, no orders, no API key).

    python -m stock_analyzer.monitor NVDA TSLA AMD
    python -m stock_analyzer.monitor NVDA --replay 2026-11-20      # replay a past day
    python -m stock_analyzer.monitor --scan 5 --giveback 0.6       # scanner picks 5, sell on 60% give-back

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

from . import notify
from .data import NY, to_3min
from .strategy import Params, giveback_price, simulate_day

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
        tp = f"take profit {entry * (1 + p.take_profit):.2f}" if p.take_profit > 0 else "no fixed take profit"
        gb = f" | give-back {p.giveback * 100:.0f}% of gain" if p.giveback > 0 else ""
        return "BUY", f"enter ~{entry:.2f} | {tp} | stop {entry * (1 - p.stop_loss):.2f}{gb}"
    pct = (day["close"].iloc[-1] / entry - 1) * 100
    peak = max(entry, float(day["close"].max()))
    detail = f"{pct:+.2f}% vs entry | best {(peak / entry - 1) * 100:+.2f}%"
    gb = giveback_price(entry, peak, p)
    if gb is not None:
        detail += f" | sell if below {gb:.2f}"
    return "HOLD", detail


JOURNAL_COLS = ["date", "ticker", "entry", "exit", "reason", "ret_pct", "best_pct", "exit_time", "settings"]


def log_trade(ticker: str, t, p: Params, path: Path = JOURNAL) -> None:
    """Append one closed paper trade as a structured row (easy to analyse later)."""
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(JOURNAL_COLS)
        settings = (f"tp={p.take_profit} sl={p.stop_loss} trail={p.trail_drop} "
                    f"giveback={p.giveback} delay={p.entry_delay}")
        w.writerow([t.date.date(), ticker, round(t.entry, 4), round(t.exit, 4), t.reason,
                    round(t.ret * 100, 3), round(t.max_run_up * 100, 3), f"{t.exit_time:%H:%M}", settings])


def day_summary(results: dict) -> str:
    """Telegram-friendly end-of-day recap. results: ticker -> Trade."""
    if not results:
        return "Day summary: no trades closed today."
    lines = ["Day summary (paper trades):"]
    for tk, t in results.items():
        lines.append(f"{tk}: {t.ret * 100:+.2f}% ({t.reason}, best {t.max_run_up * 100:+.2f}%)")
    avg = sum(t.ret for t in results.values()) / len(results) * 100
    lines.append(f"Average: {avg:+.2f}% over {len(results)} trade(s)")
    return "\n".join(lines)


def fetch_today(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(ticker, period="1d", interval="1m", progress=False,
                      auto_adjust=True, multi_level_index=False)
    if raw.empty:
        return raw
    b = to_3min(raw)
    today = pd.Timestamp.now(tz=NY).date()
    return b[b.index.date == today]  # on holidays Yahoo returns the previous session: ignore it


def wait_until(hhmm: str) -> bool:
    """Sleep until hh:mm New York time today. False if that time already passed."""
    target = pd.Timestamp.now(tz=NY).normalize() + pd.Timedelta(hhmm + ":00")
    if pd.Timestamp.now(tz=NY) >= target:
        return False
    while (now := pd.Timestamp.now(tz=NY)) < target:
        print(f"{now:%H:%M} NY: waiting until {hhmm} NY...")
        time.sleep(min(300, (target - now).total_seconds()))
    return True


def scan_tickers(n: int, target: float) -> list[str]:
    from . import scanner

    if pd.Timestamp.now(tz=NY).weekday() >= 5:
        return []  # weekend: run_live will say the market is closed
    wait_until("09:20")  # pre-market prices are most useful just before the open
    table = scanner.scan(scanner.WATCHLIST, target=target)
    picks = scanner.top(table, n)
    msg = scanner.format_message(table, picks, target)
    print(msg)
    notify.send(msg)
    return picks


def run_live(tickers: list[str], p: Params) -> None:
    if not tickers:
        print("No stocks to watch (weekend, or none passed the scanner filters).")
        return
    if notify.enabled():
        notify.send(f"Monitor started: {', '.join(tickers)}")
    done: set[str] = set()
    bought: set[str] = set()
    errors: dict[str, int] = {}
    results: dict = {}
    try:
        _loop(tickers, p, done, bought, errors, results)
    finally:
        if bought:
            msg = day_summary(results)
            print(msg)
            notify.send(msg)


def _loop(tickers, p, done, bought, errors, results) -> None:
    while len(done) < len(tickers):
        now = pd.Timestamp.now(tz=NY)
        if now.weekday() >= 5:
            print("Weekend: market closed.")
            return
        if now.time() >= pd.Timestamp("16:00").time():
            print("Market closed for today.")
            return
        if now.time() < pd.Timestamp("09:30").time():
            print(f"{now:%H:%M} NY: market not open yet; waiting...")
            time.sleep(60)
            continue
        final = now.time() >= pd.Timestamp("15:57").time()
        for tk in tickers:
            if tk in done:
                continue
            try:
                bars = fetch_today(tk)
            except Exception as e:  # flaky network / Yahoo hiccup: try again next round
                errors[tk] = errors.get(tk, 0) + 1
                print(f"{now:%H:%M} {tk}: data fetch failed ({type(e).__name__}), retrying next check")
                if errors[tk] == 5:
                    notify.send(f"Warning: {tk} data failed 5 times in a row ({type(e).__name__})")
                continue
            errors[tk] = 0
            if bars.empty:
                if now.time() > pd.Timestamp("09:50").time():
                    print(f"{tk}: no data for today after 09:50 NY (holiday?). Skipping.")
                    done.add(tk)
                continue
            action, detail = decide(bars, p, final=final)
            if action == "BUY":
                if tk in bought:  # already announced; same entry bar seen twice
                    action, detail = "HOLD", "in position"
                bought.add(tk)
            print(f"{now:%H:%M} {tk:6s} {action:5s} {detail}")
            if action in ("BUY", "SELL"):
                notify.send(f"{action} {tk}\n{detail}")
            if action == "SELL":
                t = simulate_day(bars, p, final=final)
                if t is not None:
                    log_trade(tk, t, p)
                    results[tk] = t
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
    ap.add_argument("tickers", nargs="*", help="stocks to watch (or use --scan)")
    ap.add_argument("--scan", type=int, metavar="N", help="let the scanner pick the top N stocks at 09:20 NY")
    ap.add_argument("--scan-target", type=float, default=0.03, help="scanner: rise from the open to look for (0.03 = 3%%)")
    ap.add_argument("--replay", help="YYYY-MM-DD: replay that day instead of going live")
    ap.add_argument("--delay", type=int, default=2, help="3-min bars to wait after the open")
    ap.add_argument("--stop-loss", type=float, default=0.015)
    ap.add_argument("--trail-drop", type=float, default=0.015, help="0 = off")
    ap.add_argument("--take-profit", type=float, default=0.02, help="0 = off (let winners run)")
    ap.add_argument("--giveback", type=float, default=0.0,
                    help="sell after giving back this share of the best gain, e.g. 0.6 = 60%% (0 = off)")
    ap.add_argument("--giveback-arm", type=float, default=0.005, help="give-back starts once up this much")
    a = ap.parse_args()
    params = Params(entry_delay=a.delay, stop_loss=a.stop_loss, trail_drop=a.trail_drop,
                    take_profit=a.take_profit, giveback=a.giveback, giveback_arm=a.giveback_arm)
    if not a.tickers and not a.scan:
        ap.error("give tickers or --scan N")
    if a.replay:
        replay(a.tickers[0], a.replay, params)
    else:
        try:
            run_live(a.tickers or scan_tickers(a.scan, a.scan_target), params)
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as e:
            notify.send(f"Monitor crashed: {type(e).__name__}. Restart it: bash deploy/mac_run.sh ...")
            raise
