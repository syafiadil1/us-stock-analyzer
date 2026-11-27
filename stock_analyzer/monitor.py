"""Live paper-trading monitor (alerts only, no orders, no API key).

    python -m stock_analyzer.monitor --preset recommended          # what deploy/mac_run.sh runs
    python -m stock_analyzer.monitor NVDA TSLA AMD                 # your own list, buy at the open
    python -m stock_analyzer.monitor NVDA --replay 2026-11-20 --preset recommended   # replay a day

Re-checks every 3 minutes with the SAME rules as the backtest (strategy.py), prints
WAIT / BUY / HOLD / SELL / SKIP and writes closed paper trades to paper_trades.csv.
Yahoo's free feed can lag, so treat prices as approximate.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import notify
from .data import NY, to_3min
from .strategy import (RECOMMENDED, Params, breakout_entry, gap_too_big, giveback_price, market_ok,
                       opening_range_high, simulate_day, simulate_session)

JOURNAL = Path("paper_trades.csv")
INDEX = "QQQ"  # market filter for breakout mode

PRESETS = {
    # params, how many stocks the scanner hands over, max open positions per day
    "recommended": (RECOMMENDED, 15, 5),
}


def session_close(day: dt.date) -> str:
    """NYSE closing time: 13:00 on the day after Thanksgiving and on Dec 24 (Mon-Thu)."""
    nov1 = dt.date(day.year, 11, 1)
    thanksgiving = nov1 + dt.timedelta(days=(3 - nov1.weekday()) % 7 + 21)
    if day == thanksgiving + dt.timedelta(days=1):
        return "13:00"
    if day.month == 12 and day.day == 24 and day.weekday() < 4:
        return "13:00"
    return "16:00"


def _minus(hhmm: str, minutes: int) -> str:
    h, m = map(int, hhmm.split(":"))
    t = h * 60 + m - minutes
    return f"{t // 60:02d}:{t % 60:02d}"


def _hold_detail(day: pd.DataFrame, p: Params) -> str:
    entry = day["open"].iloc[0] * (1 + p.slippage)
    pct = (day["close"].iloc[-1] / entry - 1) * 100
    peak = max(entry, float(day["close"].max()))
    detail = f"{pct:+.2f}% vs entry | best {(peak / entry - 1) * 100:+.2f}%"
    gb = giveback_price(entry, peak, p)
    if gb is not None:
        detail += f" | sell if below {gb:.2f}"
    return detail


def _buy_detail(entry: float, p: Params, extra: str = "") -> str:
    tp = f"take profit {entry * (1 + p.take_profit):.2f}" if p.take_profit > 0 else "no fixed take profit"
    gb = f" | give-back {p.giveback * 100:.0f}% of gain" if p.giveback > 0 else ""
    return f"{extra}enter ~{entry:.2f} | {tp} | stop {entry * (1 - p.stop_loss):.2f}{gb}"


def _sell_detail(t) -> str:
    return f"{t.reason} {t.ret * 100:+.2f}% (entry {t.entry:.2f} exit {t.exit:.2f})"


def decide(bars: pd.DataFrame, p: Params, final: bool = False,
           index_bars: pd.DataFrame | None = None, prev_close: float | None = None) -> tuple[str, str]:
    """Return (action, detail) for a day's 3-min bars so far.

    Actions: WAIT, BUY (fires once, on the entry bar), HOLD, SELL, SKIP (no trade today)."""
    if p.entry == "breakout":
        return _decide_breakout(bars, p, final, index_bars, prev_close)
    day = bars.iloc[p.entry_delay:]
    if len(day) < 1:
        return "WAIT", f"waiting for entry (delay {p.entry_delay} bars)"
    t = simulate_day(bars, p, final=final)
    if t is not None:
        return "SELL", _sell_detail(t)
    if len(day) == 1:
        return "BUY", _buy_detail(day["open"].iloc[0] * (1 + p.slippage), p)
    return "HOLD", _hold_detail(day, p)


def _decide_breakout(bars, p, final, index_bars, prev_close=None) -> tuple[str, str]:
    if p.market_filter:
        ok = market_ok(index_bars, p)
        if ok is None:
            return ("SKIP", "no market data") if final else ("WAIT", f"checking {INDEX} until {p.market_check}")
        if not ok:
            return "SKIP", f"{INDEX} below its open after ~10 min: no trade today"
    if gap_too_big(bars, prev_close, p):
        return "SKIP", f"gapped up {(bars['open'].iloc[0] / prev_close - 1) * 100:+.1f}% at the open"
    hi = opening_range_high(bars, p)
    if hi is None:
        return ("SKIP", "no data") if final else ("WAIT", f"opening range forming until {p.or_end}")
    k = breakout_entry(bars, p)
    if k is None:
        if final or bars.index[-1].strftime("%H:%M") >= p.last_entry:
            return "SKIP", f"no close above {hi:.2f} by {p.last_entry}"
        return "WAIT", f"waiting for a close above {hi:.2f} (opening-range high)"
    if k == len(bars):  # breakout bar just closed: buy now
        entry = float(bars["close"].iloc[-1]) * (1 + p.slippage)
        return "BUY", _buy_detail(entry, p, extra=f"broke {hi:.2f} | ")
    q = replace(p, entry_delay=0)
    t = simulate_day(bars.iloc[k:], q, final=final)
    if t is not None:
        return "SELL", _sell_detail(t)
    return "HOLD", _hold_detail(bars.iloc[k:], q)


JOURNAL_COLS = ["date", "ticker", "entry", "exit", "reason", "ret_pct", "best_pct", "exit_time", "settings"]


def log_trade(ticker: str, t, p: Params, path: Path = JOURNAL) -> None:
    """Append one closed paper trade as a structured row (easy to analyse later)."""
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(JOURNAL_COLS)
        settings = (f"entry={p.entry} tp={p.take_profit} sl={p.stop_loss} trail={p.trail_drop} "
                    f"giveback={p.giveback} arm={p.giveback_arm} chop={p.chop_flips} delay={p.entry_delay}")
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


def complete_bars(raw: pd.DataFrame, now: pd.Timestamp, bar_min: int = 3) -> pd.DataFrame:
    """3-min bars from Yahoo 1-min data, keeping ONLY bars that have fully finished.

    Yahoo labels minutes by their start and includes the minute still in progress, so the
    newest 3-min bar is often incomplete. The backtest only ever sees finished bars; the
    live monitor must too, or a half-formed bar could trigger a BUY the backtest never would."""
    b = to_3min(raw)
    if b.empty:
        return b
    idx = pd.DatetimeIndex(raw.index)
    last_min = (idx.tz_localize(NY) if idx.tz is None else idx.tz_convert(NY)).max()
    end = b.index + pd.Timedelta(minutes=bar_min)
    has_last_minute = b.index + pd.Timedelta(minutes=bar_min - 1) <= last_min
    long_past = end + pd.Timedelta(minutes=2) <= now  # thin stock with no trade in its last minute
    return b[(end <= now) & (has_last_minute | long_past)]


def fetch_today(ticker: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    import yfinance as yf

    now = now or pd.Timestamp.now(tz=NY)
    raw = yf.download(ticker, period="1d", interval="1m", progress=False,
                      auto_adjust=True, multi_level_index=False)
    if raw.empty:
        return raw
    b = complete_bars(raw, now)
    return b[b.index.date == now.date()]  # on holidays Yahoo returns the previous session: ignore it


def prev_closes(tickers: list[str]) -> dict[str, float]:
    """Yesterday's close for each ticker (for the gap rule). Missing = gap rule not applied."""
    import yfinance as yf

    out: dict[str, float] = {}
    try:
        raw = yf.download(tickers, period="7d", interval="1d", progress=False,
                          auto_adjust=True, group_by="ticker", threads=True)
    except Exception as e:
        print(f"previous-close download failed ({type(e).__name__}): gap rule off today")
        return out
    today = pd.Timestamp.now(tz=NY).date()
    for tk in tickers:
        try:
            d = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
            d = d.dropna(subset=["Close"])
            d = d[pd.DatetimeIndex(d.index).date < today]
            if not d.empty:
                out[tk] = float(d["Close"].iloc[-1])
        except KeyError:
            pass
    return out


def wait_until(hhmm: str) -> bool:
    """Sleep until hh:mm New York time today. False if that time already passed."""
    target = pd.Timestamp.now(tz=NY).normalize() + pd.Timedelta(hhmm + ":00")
    if pd.Timestamp.now(tz=NY) >= target:
        return False
    while (now := pd.Timestamp.now(tz=NY)) < target:
        print(f"{now:%H:%M} NY: waiting until {hhmm} NY...")
        time.sleep(min(300, (target - now).total_seconds()))
    return True


def scan_tickers(n: int, target: float, p: Params = Params()) -> list[str]:
    from . import scanner

    if pd.Timestamp.now(tz=NY).weekday() >= 5:
        return []  # weekend: run_live will say the market is closed
    wait_until("09:20")  # pre-market prices are most useful just before the open
    table = scanner.scan(scanner.WATCHLIST, target=target)
    picks = scanner.top(table, n)
    msg = scanner.format_message(table, picks, target)
    if p.entry == "breakout" and picks:
        msg += (f"\nWatching these. BUY only if {INDEX} is green after ~10 min AND the stock closes "
                f"above its opening-range high (09:30-{p.or_end}) before {p.last_entry} NY. Max a few BUYs.")
    print(msg)
    notify.send(msg)
    return picks


def run_live(tickers: list[str], p: Params, max_positions: int | None = None) -> None:
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
        _loop(tickers, p, done, bought, errors, results, max_positions)
    finally:
        if bought:
            msg = day_summary(results)
            print(msg)
            notify.send(msg)


def _fetch(tk, now, errors) -> pd.DataFrame | None:
    try:
        bars = fetch_today(tk)
    except Exception as e:  # flaky network / Yahoo hiccup: try again next round
        errors[tk] = errors.get(tk, 0) + 1
        print(f"{now:%H:%M} {tk}: data fetch failed ({type(e).__name__}), retrying next check")
        if errors[tk] == 5:
            notify.send(f"Warning: {tk} data failed 5 times in a row ({type(e).__name__})")
        return None
    errors[tk] = 0
    return bars


def _loop(tickers, p, done, bought, errors, results, max_positions=None) -> None:
    index_bars = None
    prev = prev_closes(tickers) if p.entry == "breakout" and p.max_gap > 0 else {}
    while len(done) < len(tickers):
        now = pd.Timestamp.now(tz=NY)
        close = session_close(now.date())
        if now.weekday() >= 5:
            print("Weekend: market closed.")
            return
        if now.strftime("%H:%M") >= close:
            print("Market closed for today.")
            return
        if now.time() < pd.Timestamp("09:30").time():
            print(f"{now:%H:%M} NY: market not open yet; waiting...")
            time.sleep(60)
            continue
        final = now.strftime("%H:%M") >= _minus(close, 3)
        if p.entry == "breakout" and p.market_filter and market_ok(index_bars, p) is None:
            got = _fetch(INDEX, now, errors)
            if got is not None and not got.empty:
                index_bars = got
        skipped: dict[str, list[str]] = {}
        for tk in tickers:
            if tk in done:
                continue
            bars = _fetch(tk, now, errors)
            if bars is None:
                continue
            if bars.empty:
                if now.time() > pd.Timestamp("09:50").time():
                    print(f"{tk}: no data for today after 09:50 NY (holiday?). Skipping.")
                    done.add(tk)
                continue
            action, detail = decide(bars, p, final=final, index_bars=index_bars, prev_close=prev.get(tk))
            if action == "BUY":
                if tk in bought:  # already announced; same entry bar seen twice
                    action, detail = "HOLD", "in position"
                elif max_positions and len(bought) >= max_positions:
                    action, detail = "SKIP", f"already {max_positions} positions today"
                else:
                    bought.add(tk)
            elif action in ("HOLD", "SELL") and tk not in bought:
                # the entry bar was missed (fetch error / restart): announce it now, late
                if max_positions and len(bought) >= max_positions:
                    action, detail = "SKIP", f"already {max_positions} positions today"
                else:
                    bought.add(tk)
                    if action == "HOLD":
                        action, detail = "BUY", f"LATE signal (missed a check), price moved: {detail}"
            print(f"{now:%H:%M} {tk:6s} {action:5s} {detail}")
            if action in ("BUY", "SELL"):
                notify.send(f"{action} {tk}\n{detail}")
            if action == "SELL":
                t = simulate_session(bars, p, index_bars=index_bars, final=final, prev_close=prev.get(tk))
                if t is not None:
                    log_trade(tk, t, p)
                    results[tk] = t
                done.add(tk)
            if action == "SKIP":
                why = (f"no breakout by {p.last_entry}" if detail.startswith("no close above")
                       else f"gapped up more than {p.max_gap * 100:.0f}% (big gap-ups tend to fade)"
                       if detail.startswith("gapped up") else detail)
                skipped.setdefault(why, []).append(tk)
                done.add(tk)
        for why, tks in skipped.items():
            notify.send(f"SKIP {', '.join(tks)}\n{why}")
        if final:
            break
        # wake 15 s after the next 3-minute mark, so Yahoo has published the bar that just ended
        time.sleep(180 - (time.time() % 180) + 15)


def replay(ticker: str, day: str, p: Params) -> None:
    from .data import fetch_intraday

    d = pd.Timestamp(day).date()
    all_bars = fetch_intraday(ticker)
    bars = all_bars[all_bars.index.date == d]
    if bars.empty:
        raise SystemExit(f"No data for {ticker} on {day}")
    idx, pc = None, None
    if p.entry == "breakout" and p.market_filter:
        q = fetch_intraday(INDEX)
        idx = q[q.index.date == d]
    if p.entry == "breakout" and p.max_gap > 0:
        import yfinance as yf
        dd = yf.download(ticker, start=pd.Timestamp(d) - pd.Timedelta(days=10), end=pd.Timestamp(d),
                         interval="1d", progress=False, auto_adjust=True, multi_level_index=False)
        pc = float(dd["Close"].iloc[-1]) if not dd.empty else None
    for n in range(1, len(bars) + 1):
        sub_idx = None if idx is None else idx[idx.index <= bars.index[n - 1]]
        action, detail = decide(bars.iloc[:n], p, final=(n == len(bars)), index_bars=sub_idx, prev_close=pc)
        print(f"{bars.index[n - 1]:%H:%M} {ticker} {action:5s} {detail}")
        if action in ("SELL", "SKIP"):
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="stocks to watch (or use --scan / --preset)")
    ap.add_argument("--preset", choices=sorted(PRESETS),
                    help="tested settings (overrides the strategy options below)")
    ap.add_argument("--scan", type=int, metavar="N", help="let the scanner pick the top N stocks at 09:20 NY")
    ap.add_argument("--scan-target", type=float, default=0.03, help="scanner: rise from the open to look for (0.03 = 3%%)")
    ap.add_argument("--max-positions", type=int, help="at most this many BUYs per day")
    ap.add_argument("--replay", help="YYYY-MM-DD: replay that day instead of going live")
    ap.add_argument("--entry", choices=["open", "breakout"], default="open")
    ap.add_argument("--delay", type=int, default=2, help="3-min bars to wait after the open (entry=open)")
    ap.add_argument("--stop-loss", type=float, default=0.015)
    ap.add_argument("--trail-drop", type=float, default=0.015, help="0 = off")
    ap.add_argument("--take-profit", type=float, default=0.02, help="0 = off (let winners run)")
    ap.add_argument("--giveback", type=float, default=0.0,
                    help="sell after giving back this share of the best gain, e.g. 0.6 = 60%% (0 = off)")
    ap.add_argument("--giveback-arm", type=float, default=0.01, help="give-back starts once up this much")
    ap.add_argument("--no-chop", action="store_true", help="turn off the CHOPPY exit")
    a = ap.parse_args()
    if a.preset:
        params, scan_n, max_pos = PRESETS[a.preset]
        scan_n = a.scan or scan_n
        max_pos = a.max_positions or max_pos
    else:
        params = Params(entry=a.entry, entry_delay=a.delay if a.entry == "open" else 0,
                        stop_loss=a.stop_loss, trail_drop=a.trail_drop, take_profit=a.take_profit,
                        giveback=a.giveback, giveback_arm=a.giveback_arm,
                        chop_flips=0 if a.no_chop else Params.chop_flips)
        scan_n, max_pos = a.scan, a.max_positions
    if not a.tickers and not scan_n:
        ap.error("give tickers, --scan N or --preset")
    if a.replay:
        if not a.tickers:
            ap.error("--replay needs a ticker")
        replay(a.tickers[0], a.replay, params)
    else:
        try:
            run_live(a.tickers or scan_tickers(scan_n, a.scan_target, params), params, max_pos)
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as e:
            notify.send(f"Monitor crashed: {type(e).__name__}. Restart it: bash deploy/mac_run.sh")
            raise
