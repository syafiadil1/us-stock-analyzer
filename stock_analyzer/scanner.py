"""Find stocks that have OFTEN risen X% after the open, and check them before the bell.

    python -m stock_analyzer.scanner                    # default watchlist, 3% target
    python -m stock_analyzer.scanner AAPL NVDA PLTR --target 0.02 --top 5 --telegram

Per stock (last ~3 months of daily bars):
  hit_rate     share of days the high reached >= target above the open
  avg_range_%  average high-low range (how much it moves in a day)
  dollar_vol_M average daily traded value, in $ millions (can you get in and out?)
  trend_up     last close above its 20-day average
  premkt_%     pre-market price vs yesterday's close (only meaningful before 09:30 NY)

Ranking = hit_rate, after dropping illiquid names and hit rates under 20%.
On 2 years of daily data a high past hit rate DID predict more +3% days (42% vs 7%),
but those stocks also fall more: the average open-to-close return was ~0.
So it finds stocks that MOVE, not stocks that will rise. The exit rules do the rest.
"""
from __future__ import annotations

import argparse

import pandas as pd

# ~100 liquid US stocks across sectors. Edit freely. A wider list gave better picks in the
# 2-year test (top-5 hit +3% on 50% of days with ~100 names vs 41% with 30).
WATCHLIST = [
    "NVDA", "TSLA", "AMD", "PLTR", "SMCI", "COIN", "MSTR", "META", "AMZN", "AAPL",
    "MSFT", "GOOGL", "NFLX", "AVGO", "MU", "ARM", "SHOP", "UBER", "SNOW", "CRWD",
    "HOOD", "SOFI", "RIVN", "MARA", "RIOT", "AFRM", "ROKU", "DKNG", "INTC", "ANET",
    "UPST", "APP", "IONQ", "RGTI", "SOUN", "CVNA", "TTD", "DDOG", "NET", "ZS",
    "PANW", "MRVL", "QCOM", "LRCX", "AMAT", "KLAC", "ON", "WDC", "STX", "DELL",
    "HPE", "ORCL", "CRM", "ADBE", "NOW", "PYPL", "XYZ", "BABA", "PDD", "JD",
    "NIO", "LCID", "F", "GM", "BA", "DAL", "UAL", "CCL", "ABNB", "DASH",
    "LYFT", "PINS", "SNAP", "RDDT", "U", "PATH", "AI", "BBAI", "CLSK", "WULF",
    "CIFR", "IREN", "OKLO", "SMR", "VST", "CEG", "ENPH", "FSLR", "LLY", "NVO",
    "MRNA", "CELH", "ELF", "CAVA", "CHWY", "GME", "AMC", "TGT", "WMT", "COST",
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


def _split(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yf.download(group_by='ticker') output -> {ticker: frame}, skipping empty ones."""
    if raw is None or raw.empty:
        return {}
    if not isinstance(raw.columns, pd.MultiIndex):  # single ticker
        return {tickers[0]: raw.dropna(how="all")}
    have = set(raw.columns.get_level_values(0))
    return {tk: raw[tk].dropna(how="all") for tk in tickers if tk in have and not raw[tk].dropna(how="all").empty}


def premarket_changes(prev_close: dict[str, float]) -> dict[str, float]:
    """Latest pre/regular-market price today vs yesterday's close, for many tickers at once."""
    import yfinance as yf

    tks = list(prev_close)
    raw = yf.download(tks, period="1d", interval="1m", prepost=True, progress=False,
                      auto_adjust=True, group_by="ticker", threads=True)
    today = pd.Timestamp.now(tz="America/New_York").date()
    out = {}
    for tk, m in _split(raw, tks).items():
        idx = pd.DatetimeIndex(m.index).tz_convert("America/New_York")
        m = m[idx.date == today].dropna(subset=["Close"])
        if not m.empty and prev_close[tk]:
            out[tk] = float(m["Close"].iloc[-1]) / prev_close[tk] - 1
    return out


def scan(tickers: list[str], target: float = 0.03, premarket: bool = True) -> pd.DataFrame:
    import yfinance as yf

    try:
        raw = yf.download(tickers, period="3mo", interval="1d", progress=False,
                          auto_adjust=True, group_by="ticker", threads=True)
    except Exception as e:
        print(f"daily download failed ({type(e).__name__})")
        return pd.DataFrame()
    rows = {}
    for tk, df in _split(raw, tickers).items():
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) >= 20:
            rows[tk] = score(df, target)
    if not rows:
        return pd.DataFrame()
    pm = {}
    if premarket:
        try:
            pm = premarket_changes({tk: r["last_close"] for tk, r in rows.items()})
        except Exception as e:
            print(f"pre-market download failed ({type(e).__name__})")
    for tk, r in rows.items():
        r["premkt_%"] = round(pm[tk] * 100, 2) if tk in pm else None
    return pd.DataFrame(rows).T.sort_values("hit_rate", ascending=False)


def top(table: pd.DataFrame, n: int, min_dollar_vol_m: float = MIN_DOLLAR_VOL_M,
        min_hit_rate: float = MIN_HIT_RATE, require_uptrend: bool = False) -> list[str]:
    """Best n by hit rate among liquid stocks that hit the target often enough.

    The uptrend filter is OFF by default: on 2 years of daily data (30 stocks) it did not
    raise the next-day hit rate (31% with it vs 36% without)."""
    if table.empty:
        return []
    ok = table[(table["dollar_vol_M"] >= min_dollar_vol_m) & (table["hit_rate"] >= min_hit_rate)]
    if require_uptrend:
        ok = ok[ok["trend_up"] == True]  # noqa: E712
    return list(ok.sort_values("hit_rate", ascending=False).index[:n])


def format_message(table: pd.DataFrame, picks: list[str], target: float) -> str:
    if not picks:
        return "Scanner: no stock passed the filters (liquid, hits target often) today."
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
