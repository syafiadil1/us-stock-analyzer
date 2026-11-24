"""Download 1-minute data, keep regular hours, resample to 3-minute bars."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

NY = "America/New_York"


def to_3min(df: pd.DataFrame) -> pd.DataFrame:
    """1-minute OHLCV (tz-aware index) -> regular-session 3-minute OHLCV."""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize(NY) if df.index.tz is None else df.index.tz_convert(NY)
    df = df.between_time("09:30", "15:59")
    out = df.resample("3min", origin="start_day", offset="9h30min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open", "close"])


def fetch_intraday(ticker: str, days: int = 28, cache_dir: str | None = ".cache") -> pd.DataFrame:
    """Fetch ~`days` of 1-minute bars via yfinance (Yahoo only keeps ~30 days)."""
    import yfinance as yf

    cache = Path(cache_dir) / f"{ticker}_1m.csv" if cache_dir else None
    if cache and cache.exists():
        df = pd.read_csv(cache, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)  # cache mixes EST/EDT offsets
        return to_3min(df)

    end = pd.Timestamp.now(tz="UTC")
    chunks = []
    # Yahoo allows at most 8 days of 1m data per request, so pull in 7-day slices.
    for k in range(0, days, 7):
        s, e = end - pd.Timedelta(days=min(k + 7, days)), end - pd.Timedelta(days=k)
        part = yf.download(ticker, start=s, end=e, interval="1m", progress=False,
                           auto_adjust=True, multi_level_index=False)
        if not part.empty:
            chunks.append(part)
    if not chunks:
        raise RuntimeError(f"No intraday data for {ticker} (network blocked or bad ticker?)")
    raw = pd.concat(chunks).sort_index()
    raw = raw[~raw.index.duplicated()]
    if cache:
        cache.parent.mkdir(exist_ok=True)
        raw.to_csv(cache)
    return to_3min(raw)
