"""Find stocks that have OFTEN risen X% after the open, and check them before the bell.

    python -m stock_analyzer.scanner                    # default watchlist, 3% target
    python -m stock_analyzer.scanner AAPL NVDA PLTR --target 0.02 --top 5 --telegram

Per stock (last ~3 months of daily bars):
  hit_rate     share of days the high reached >= target above the open
  avg_range_%  average high-low range (how much it moves in a day)
  dollar_vol_M average daily traded value, in $ millions (can you get in and out?)
  trend_up     last close above its 20-day average
  premkt_%     pre-market price vs yesterday's close (only meaningful before 09:30 NY)

Ranking = hit_rate, after dropping illiquid names, downtrends and hit rates under 20%.
This is history, not a forecast: a high hit rate does NOT mean it will rise today.
"""
from __future__ import annotations

import argparse

import pandas as pd

# Liquid, fast-moving US stocks. Edit freely.
WATCHLIST = [
    "NVDA", "TSLA", "AMD", "PLTR", "SMCI", "COIN", "MSTR", "META", "AMZN", "AAPL",
    "MSFT", "GOOGL", "NFLX", "AVGO", "MU", "ARM", "SHOP", "UBER", "SNOW", "CRWD",
    "HOOD", "SOFI", "RIVN", "MARA", "RIOT", "AFRM", "ROKU", "DKNG", "INTC", "ANET",
]

MIN_DOLLAR_VOL_M = 200.0
MIN_HIT_RATE = 0.20


def score(daily: pd.DataFrame, target: float = 0.03) -> dict:
    d = daily.copy()
    d.columns = [str(c).lower() for c in d.columns]
    up = d["high"] / d["open"] - 1
    sma20 = d["close"].rolling(20).mean()
    return {
        "days": len(d),
        "hit_rate": float((up >= target).mean()),
        "avg_range_%": float(((d["high"] - d["low"]) / d["open"]).mean() * 100),
        "dollar_vol_M": float((d["close"] * d["volume"]).mean() / 1e6),
        "trend_up": bool(len(d) >= 20 and d["close"].iloc[-1] > sma20.iloc[-1]),
        "last_close": float(d["close"].iloc[-1]),
    }


def premarket_change(ticker: str, prev_close: float) -> float | None:
    """Latest pre/regular-market price today vs yesterday's close, or None."""
    import yfinance as yf

    m = yf.download(ticker, period="1d", interval="1m", prepost=True, progress=False,
                    auto_adjust=True, multi_level_index=False)
    if m.empty:
        return None
    today = pd.Timestamp.now(tz="America/New_York").date()
    idx = pd.DatetimeIndex(m.index).tz_convert("America/New_York")
    m = m[idx.date == today]
    if m.empty:
        return None
    return float(m["Close"].iloc[-1]) / prev_close - 1


def scan(tickers: list[str], target: float = 0.03, premarket: bool = True) -> pd.DataFrame:
    import yfinance as yf

    rows = {}
    for tk in tickers:
        try:
            df = yf.download(tk, period="3mo", interval="1d", progress=False,
                             auto_adjust=True, multi_level_index=False)
        except Exception as e:  # one bad ticker should not stop the scan
            print(f"{tk}: download failed ({type(e).__name__})")
            continue
        if df.empty or len(df) < 20:
            continue
        r = score(df, target)
        r["premkt_%"] = None
        if premarket:
            try:
                ch = premarket_change(tk, r["last_close"])
                r["premkt_%"] = None if ch is None else round(ch * 100, 2)
            except Exception:
                pass
        rows[tk] = r
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).T.sort_values("hit_rate", ascending=False)


def top(table: pd.DataFrame, n: int, min_dollar_vol_m: float = MIN_DOLLAR_VOL_M,
        min_hit_rate: float = MIN_HIT_RATE) -> list[str]:
    """Best n by hit rate among liquid stocks in an uptrend that hit the target often enough."""
    if table.empty:
        return []
    ok = table[(table["dollar_vol_M"] >= min_dollar_vol_m) & (table["trend_up"] == True)  # noqa: E712
               & (table["hit_rate"] >= min_hit_rate)]
    return list(ok.sort_values("hit_rate", ascending=False).index[:n])


def format_message(table: pd.DataFrame, picks: list[str], target: float) -> str:
    if not picks:
        return "Scanner: no stock passed the filters (liquid, uptrend, hits target often) today."
    lines = [f"Scanner: stocks that OFTEN rose {target * 100:.0f}%+ after the open (last 3 months)"]
    for tk in picks:
        r = table.loc[tk]
        pm = r["premkt_%"]
        pm_txt = "n/a" if pm is None or pd.isna(pm) else f"{pm:+.2f}%"
        lines.append(f"{tk}: hit {r['hit_rate'] * 100:.0f}% of days | range {r['avg_range_%']:.1f}% "
                     f"| pre-market {pm_txt}")
    lines.append("History only, not a forecast or advice.")
    return "\n".join(lines)


def main() -> None:
    from . import notify

    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="default: built-in watchlist")
    ap.add_argument("--target", type=float, default=0.03, help="rise from the open to count (0.03 = 3%%)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--telegram", action="store_true", help="also send the top list to Telegram")
    a = ap.parse_args()

    table = scan(a.tickers or WATCHLIST, a.target)
    if table.empty:
        raise SystemExit("No data (network blocked or bad tickers?)")
    show = table.copy()
    show["hit_rate"] = (show["hit_rate"].astype(float) * 100).round(0)
    print(show.drop(columns=["last_close"]).round(2).to_string())
    picks = top(table, a.top)
    msg = format_message(table, picks, a.target)
    print("\n" + msg)
    if a.telegram:
        print("Telegram sent:", notify.send(msg))


if __name__ == "__main__":
    main()
