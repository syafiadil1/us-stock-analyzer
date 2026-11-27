"""Live paper-trading monitor (alerts only, no orders, no API key).

    python -m stock_analyzer.monitor --preset recommended          # what deploy/mac_run.sh runs
    python -m stock_analyzer.monitor NVDA TSLA AMD                 # your own list, buy at the open
    python -m stock_analyzer.monitor NVDA --replay 2026-11-20 --preset recommended   # replay a day

Re-checks every 3 minutes with the SAME rules as the backtest (strategy.py), prints
WAIT / BUY / HOLD / SELL / SKIP and writes closed paper trades to paper_trades.csv.
Each time a stock bought today reaches a new +1% level (--up-step 0.01; 0 = off), at any time of
day, it sends a PRICE UP alert: +1%, +2%, +3% ... since the buy, or since the sale once sold.
Optional: --update-every 60 adds an update on every bought stock on each clock hour.
Yahoo's free feed can lag, so treat prices as approximate.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
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
MYT = "Asia/Kuala_Lumpur"

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


def _held(bars: pd.DataFrame, p: Params) -> pd.DataFrame:
    """The bars since the paper entry (empty while not in, or on the buy bar itself)."""
    if p.entry == "breakout":
        k = breakout_entry(bars, p)
        return bars.iloc[len(bars):] if k is None else bars.iloc[k:]
    return bars.iloc[p.entry_delay:]


def entry_point(bars: pd.DataFrame, p: Params) -> float | None:
    """Paper entry price, the same one the simulator uses (next bar's open + costs)."""
    held = _held(bars, p)
    if not held.empty:
        return float(held["open"].iloc[0]) * (1 + p.slippage)
    if p.entry == "breakout" and len(bars) and breakout_entry(bars, p) == len(bars):
        return float(bars["close"].iloc[-1]) * (1 + p.slippage)  # buying now, ~at this close
    return None


def sell_line(bars: pd.DataFrame, p: Params) -> float | None:
    """Price where the rules would sell if a 3-min bar closes at or below it (stop or give-back)."""
    entry = entry_point(bars, p)
    if entry is None:
        return None
    peak = max([entry] + _held(bars, p)["close"].astype(float).tolist())
    lines = [entry * (1 - p.stop_loss)] if p.stop_loss > 0 else []
    gb = giveback_price(entry, peak, p)
    if gb is not None:
        lines.append(gb)
    if p.trail_drop > 0 and peak > entry:
        lines.append(peak * (1 - p.trail_drop))
    return max(lines) if lines else None


def _pct(now: float, then: float) -> str:
    return f"{(now / then - 1) * 100:+.2f}%"


class HourlyReport:
    """Every `every` minutes (60 = on each clock hour), one message on every stock bought today.

    Holding: price now, change in the last hour, change since the buy, current sell line.
    Sold:    the paper result, then how the price moved AFTER the sale (was selling right?).
    The first update after a buy (or sale) shows only 'since buy' ('since sell'): a 'last hour'
    figure would mix in the move from before you were in."""

    def __init__(self, every: int = 60):
        self.every = every
        self.pos: dict[str, dict] = {}
        self.next: pd.Timestamp | None = None

    def _mark(self, now: pd.Timestamp) -> pd.Timestamp:
        return now.floor(f"{self.every}min")

    def bought(self, tk: str, now: pd.Timestamp) -> None:
        if self.every <= 0 or tk in self.pos:
            return
        self.pos[tk] = dict(entry=None, price=None, ref=None, line=None, sold=None, sell_px=None)
        if self.next is None:
            self.next = self._mark(now) + pd.Timedelta(minutes=self.every)

    def update(self, tk: str, price: float | None, entry: float | None = None, line: float | None = None) -> None:
        s = self.pos.get(tk)
        if s is None:
            return
        s["price"] = price
        if entry is not None:
            s["entry"] = entry
        if s["sold"] is None:
            s["line"] = line

    def sold(self, tk: str, t, sell_px: float) -> None:
        s = self.pos.get(tk)
        if s is not None:
            s.update(sold=t, sell_px=sell_px, price=sell_px, entry=t.entry, ref=None, line=None)

    def drop(self, tk: str) -> None:
        self.pos.pop(tk, None)

    def sold_tickers(self) -> list[str]:
        return [tk for tk, s in self.pos.items() if s["sold"] is not None]

    def due(self, now: pd.Timestamp, final: bool = False) -> bool:
        return bool(self.pos) and self.next is not None and now >= self.next and not final

    def waiting(self, until: pd.Timestamp) -> bool:
        """True while another update is still coming before `until` (the last check of the day)."""
        return bool(self.pos) and self.next is not None and self.next < until

    def message(self, now: pd.Timestamp) -> str:
        mark = self._mark(now)
        my = mark.tz_convert(MYT).strftime("%-I:%M %p").lower()
        lines = [f"HOURLY UPDATE {mark:%H:%M} NY ({my} Malaysia)"]
        for tk, s in sorted(self.pos.items(), key=lambda kv: kv[1]["sold"] is not None):
            px, t = s["price"], s["sold"]
            if t is not None:
                head = f"{tk} SOLD ({t.reason} {t.ret * 100:+.2f}%)"  # since sell = vs the market price
                if px is None:
                    lines.append(f"{head}: no price right now")
                    continue
                parts = [f"{head}: now {px:.2f}"]
                if s["ref"] is not None:
                    parts.append(f"last hour {_pct(px, s['ref'])}")
                parts.append(f"since sell {_pct(px, s['sell_px'])}")
            elif px is None or s["entry"] is None:
                lines.append(f"{tk} holding: no price right now")
                continue
            else:
                parts = [f"{tk} holding: now {px:.2f}"]
                if s["ref"] is not None:
                    parts.append(f"last hour {_pct(px, s['ref'])}")
                parts.append(f"since buy {_pct(px, s['entry'])} (in at ~{s['entry']:.2f})")
                if s["line"] is not None:
                    parts.append(f"sell if below {s['line']:.2f}")
            lines.append(" | ".join(parts))
            s["ref"] = px
        self.next = mark + pd.Timedelta(minutes=self.every)
        return "\n".join(lines)


class UpAlerts:
    """Alert each time a bought stock reaches a NEW +step level (default every +1%), any time of day.

    Holding: measured from the buy price (+1%, +2%, +3% ... each level once).
    Sold:    measured from the sale price, so you see what happened after selling (not a BUY).
    A jump over several levels in one 3-min bar gives one alert. Ups only: the downside is
    already covered by the SELL rules (stop / give-back)."""

    def __init__(self, step: float = 0.01, after_sale: bool = True):
        self.step, self.after_sale = step, after_sale
        self.pos: dict[str, dict] = {}

    def bought(self, tk: str) -> None:
        if self.step > 0 and tk not in self.pos:
            self.pos[tk] = dict(entry=None, level=0, sold=None, sell_px=None)

    def sold(self, tk: str, t, sell_px: float) -> None:
        if not self.after_sale:
            self.pos.pop(tk, None)
            return
        s = self.pos.get(tk)
        if s is not None:
            s.update(sold=t, sell_px=sell_px, level=0)

    def drop(self, tk: str) -> None:
        self.pos.pop(tk, None)

    def sold_tickers(self) -> list[str]:
        return [tk for tk, s in self.pos.items() if s["sold"] is not None]

    def watching(self) -> bool:
        """True while a sold stock is still being watched for after-sale rises."""
        return bool(self.sold_tickers())

    def check(self, tk: str, price: float | None, entry: float | None = None,
              line: float | None = None) -> str | None:
        """One alert line if `price` reached a new level, else None."""
        s = self.pos.get(tk)
        if s is None or price is None:
            return None
        if entry is not None and s["sold"] is None:
            s["entry"] = entry
        base = s["sell_px"] if s["sold"] is not None else s["entry"]
        if base is None:
            return None
        gain = price / base - 1
        level = math.floor(gain / self.step + 1e-9)
        if level <= s["level"]:
            return None
        s["level"] = level
        if s["sold"] is None:
            txt = f"{tk} +{gain * 100:.2f}% since buy: now {price:.2f} (in at ~{base:.2f})"
            return txt + (f" | sell if below {line:.2f}" if line is not None else "")
        t = s["sold"]
        return (f"{tk} +{gain * 100:.2f}% since you sold ({t.reason} {t.ret * 100:+.2f}%): now {price:.2f}"
                " | already sold, this is not a new BUY")


def up_message(now: pd.Timestamp, lines: list[str]) -> str:
    my = now.tz_convert(MYT).strftime("%-I:%M %p").lower()
    return "\n".join([f"PRICE UP ({now:%H:%M} NY / {my} Malaysia)"] + lines)


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


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=NY)


def _last_check(now: pd.Timestamp) -> pd.Timestamp:
    """Time of the day's final check (3 minutes before the close)."""
    return now.normalize() + pd.Timedelta(_minus(session_close(now.date()), 3) + ":00")


def run_live(tickers: list[str], p: Params, max_positions: int | None = None, update_every: int = 0,
             up_step: float = 0.01, after_sale: bool = True) -> None:
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
        _loop(tickers, p, done, bought, errors, results, max_positions, HourlyReport(update_every),
              UpAlerts(up_step, after_sale))
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


def _loop(tickers, p, done, bought, errors, results, max_positions=None, report=None, ups=None) -> None:
    report = report or HourlyReport(0)
    ups = ups or UpAlerts(0)
    index_bars = None
    prev = prev_closes(tickers) if p.entry == "breakout" and p.max_gap > 0 else {}
    # after the last sale, keep watching sold stocks (after-sale rises, hourly "since sell")
    while len(done) < len(tickers) or ups.watching() or report.waiting(_last_check(_now())):
        now = _now()
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
        up_lines: list[str] = []
        prices: dict[str, float | None] = {}  # fetched this round (sold stocks reuse it)
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
            if tk in bought:
                report.bought(tk, now)
                ups.bought(tk)
            prices[tk] = float(bars["close"].iloc[-1])
            if action == "SELL":
                t = simulate_session(bars, p, index_bars=index_bars, final=final, prev_close=prev.get(tk))
                if t is not None:
                    log_trade(tk, t, p)
                    results[tk] = t
                    report.sold(tk, t, t.exit / (1 - p.slippage))  # market price, before costs
                    ups.sold(tk, t, t.exit / (1 - p.slippage))
                else:
                    report.drop(tk)
                    ups.drop(tk)
                done.add(tk)
            elif action in ("BUY", "HOLD") and tk in bought:
                entry, line = entry_point(bars, p), sell_line(bars, p)
                report.update(tk, prices[tk], entry, line)
                if (msg := ups.check(tk, prices[tk], entry, line)) is not None:
                    up_lines.append(msg)
            if action == "SKIP":
                why = (f"no breakout by {p.last_entry}" if detail.startswith("no close above")
                       else f"gapped up more than {p.max_gap * 100:.0f}% (big gap-ups tend to fade)"
                       if detail.startswith("gapped up") else detail)
                skipped.setdefault(why, []).append(tk)
                done.add(tk)
        for why, tks in skipped.items():
            notify.send(f"SKIP {', '.join(tks)}\n{why}")
        # sold stocks are no longer run through the rules: fetch a fresh price when needed
        hourly = report.due(now, final)
        watch = set(ups.sold_tickers()) if not final else set()
        if hourly:
            watch |= set(report.sold_tickers())
        for tk in sorted(watch - set(prices)):
            b = _fetch(tk, now, errors)
            prices[tk] = None if b is None or b.empty else float(b["close"].iloc[-1])
        for tk in ups.sold_tickers() if not final else []:
            if (msg := ups.check(tk, prices.get(tk))) is not None:
                up_lines.append(msg)
        if up_lines:
            msg = up_message(now, up_lines)
            print(msg)
            notify.send(msg)
        if hourly:
            for tk in report.sold_tickers():
                report.update(tk, prices.get(tk))
            msg = report.message(now)
            print(msg)
            notify.send(msg)
        if final:
            break
        # wake 15 s after the next 3-minute mark, so Yahoo has published the bar that just ended
        time.sleep(180 - (time.time() % 180) + 15)


def replay(ticker: str, day: str, p: Params, update_every: int = 0, up_step: float = 0.01,
           after_sale: bool = True) -> None:
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
    report, ups, sold = HourlyReport(update_every), UpAlerts(up_step, after_sale), False
    for n in range(1, len(bars) + 1):
        sub, final = bars.iloc[:n], n == len(bars)
        now = bars.index[n - 1] + pd.Timedelta(minutes=3)  # when the live monitor would see this bar
        price, up = float(sub["close"].iloc[-1]), None
        if sold:  # only the after-sale updates are left
            report.update(ticker, price)
            up = None if final else ups.check(ticker, price)
        else:
            sub_idx = None if idx is None else idx[idx.index <= bars.index[n - 1]]
            action, detail = decide(sub, p, final=final, index_bars=sub_idx, prev_close=pc)
            print(f"{bars.index[n - 1]:%H:%M} {ticker} {action:5s} {detail}")
            if action == "SKIP":
                break
            if action in ("BUY", "HOLD", "SELL"):
                report.bought(ticker, now)
                ups.bought(ticker)
            if action == "SELL":
                t = simulate_session(sub, p, index_bars=sub_idx, final=final, prev_close=pc)
                if t is None:
                    break
                report.sold(ticker, t, t.exit / (1 - p.slippage))
                ups.sold(ticker, t, t.exit / (1 - p.slippage))
                sold = True
            elif action in ("BUY", "HOLD"):
                entry, line = entry_point(sub, p), sell_line(sub, p)
                report.update(ticker, price, entry, line)
                up = ups.check(ticker, price, entry, line)
        if up is not None:
            print(up_message(now, [up]))
        if report.due(now, final=now >= _last_check(now)):
            print(report.message(now))
        if sold and not ups.watching() and not report.waiting(_last_check(now)):  # nothing left to show
            break

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="stocks to watch (or use --scan / --preset)")
    ap.add_argument("--preset", choices=sorted(PRESETS),
                    help="tested settings (overrides the strategy options below)")
    ap.add_argument("--scan", type=int, metavar="N", help="let the scanner pick the top N stocks at 09:20 NY")
    ap.add_argument("--scan-target", type=float, default=0.03, help="scanner: rise from the open to look for (0.03 = 3%%)")
    ap.add_argument("--max-positions", type=int, help="at most this many BUYs per day")
    ap.add_argument("--up-step", type=float, default=0.01, metavar="X",
                    help="alert each time a bought stock is up another X, any time (0.01 = every +1%%; 0 = off)")
    ap.add_argument("--no-after-sale", action="store_true",
                    help="no PRICE UP alerts for stocks already sold today")
    ap.add_argument("--update-every", type=int, default=0, metavar="MIN",
                    help="also an update on bought stocks every MIN minutes on the clock, e.g. 60 (0 = off)")
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
        replay(a.tickers[0], a.replay, params, a.update_every, a.up_step, not a.no_after_sale)
    else:
        try:
            run_live(a.tickers or scan_tickers(scan_n, a.scan_target, params), params, max_pos,
                     a.update_every, a.up_step, not a.no_after_sale)
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as e:
            notify.send(f"Monitor crashed: {type(e).__name__}. Restart it: bash deploy/mac_run.sh")
            raise
