"""Keep intraday data that Yahoo would otherwise delete (1-min: ~30 days, 5-min: ~60 days).

    python -m stock_analyzer.archive              # add the last few days (run after the close)
    python -m stock_analyzer.archive --backfill   # first time: grab everything Yahoo still has
    python -m stock_analyzer.archive --info       # what is stored

Files: data/archive/<interval>/<TICKER>.csv.gz, merged and de-duplicated on every run.
After a few months this gives far more days to test on than Yahoo's 60-day window,
which is the only honest way to make the backtest numbers more accurate.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .data import NY
from .scanner import WATCHLIST

ROOT = Path("data/archive")
EXTRA = ["QQQ", "SPY"]


def path_for(ticker: str, interval: str, root: Path = ROOT) -> Path:
    return root / interval / f"{ticker}.csv.gz"


def load(ticker: str, interval: str = "1m", root: Path = ROOT) -> pd.DataFrame:
    """Stored bars for one ticker (lower-case OHLCV columns, New York time index)."""
    f = path_for(ticker, interval, root)
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(NY)
    return df


def merge_save(ticker: str, interval: str, new: pd.DataFrame, root: Path = ROOT) -> int:
    """Merge new bars into the stored file. Returns how many rows the file now has."""
    if new is None or new.empty:
        return len(load(ticker, interval, root))
    new = new.copy()
    new.columns = [str(c).lower() for c in new.columns]
    new = new[[c for c in ["open", "high", "low", "close", "volume"] if c in new.columns]]
    new = new.dropna(subset=["open", "close"])
    idx = pd.DatetimeIndex(new.index)
    new.index = idx.tz_localize(NY) if idx.tz is None else idx.tz_convert(NY)
    old = load(ticker, interval, root)
    both = pd.concat([old, new]) if not old.empty else new
    both = both[~both.index.duplicated(keep="last")].sort_index()
    f = path_for(ticker, interval, root)
    f.parent.mkdir(parents=True, exist_ok=True)
    out = both.copy()
    out.index = out.index.tz_convert("UTC")  # one unambiguous offset in the file
    out.to_csv(f, compression="gzip")
    return len(both)


def _download(tickers: list[str], **kw) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    raw = yf.download(tickers, progress=False, auto_adjust=True, group_by="ticker", threads=True, **kw)
    out = {}
    if raw is None or raw.empty:
        return out
    for tk in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if tk not in raw.columns.get_level_values(0):
                continue
            d = raw[tk]
        else:
            d = raw
        d = d.dropna(how="all")
        if not d.empty:
            out[tk] = d
    return out


def update(tickers: list[str], backfill: bool = False, root: Path = ROOT) -> dict[str, int]:
    """Download and merge. Normal run: last 7 days of 1-min + 5-min. Backfill: all Yahoo keeps."""
    counts: dict[str, int] = {}
    end = pd.Timestamp.now(tz="UTC")
    # 1-minute: Yahoo serves max 8 days per request and keeps ~30 days
    spans = [(end - pd.Timedelta(days=k + 7), end - pd.Timedelta(days=k)) for k in range(0, 28 if backfill else 7, 7)]
    for s, e in spans:
        for tk, d in _download(tickers, start=s, end=e, interval="1m").items():
            counts[f"1m:{tk}"] = merge_save(tk, "1m", d, root)
    for tk, d in _download(tickers, period="60d" if backfill else "7d", interval="5m").items():
        counts[f"5m:{tk}"] = merge_save(tk, "5m", d, root)
    return counts


def info(root: Path = ROOT) -> str:
    lines = []
    for interval in ["1m", "5m"]:
        files = sorted((root / interval).glob("*.csv.gz")) if (root / interval).exists() else []
        if not files:
            lines.append(f"{interval}: nothing stored yet")
            continue
        days = set()
        for f in files[:3] + [path_for("QQQ", interval, root)]:
            if f.exists():
                days |= set(load(f.name.split(".")[0], interval, root).index.date)
        span = f"{min(days)} to {max(days)}, {len(days)} trading days" if days else "?"
        size = sum(f.stat().st_size for f in files) / 1e6
        lines.append(f"{interval}: {len(files)} tickers, {span}, {size:.1f} MB")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="first run: everything Yahoo still has")
    ap.add_argument("--info", action="store_true")
    a = ap.parse_args()
    if not a.info:
        c = update(WATCHLIST + EXTRA, backfill=a.backfill)
        print(f"archived {sum(1 for k in c if k.startswith('1m'))} tickers (1-min) "
              f"and {sum(1 for k in c if k.startswith('5m'))} tickers (5-min)")
    print(info())


if __name__ == "__main__":
    main()
