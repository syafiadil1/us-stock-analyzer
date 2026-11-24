"""Pick stocks that often move 2%+ after the open (from daily data).

    python -m stock_analyzer.scanner AAPL NVDA TSLA AMD PLTR SMCI

hit_rate = share of past days where high reached >= +2% above the open.
This is only history; a high score does not guarantee tomorrow.
"""
from __future__ import annotations

import sys

import pandas as pd


def score(daily: pd.DataFrame, target: float = 0.02) -> dict:
    d = daily.copy()
    d.columns = [str(c).lower() for c in d.columns]
    up = d["high"] / d["open"] - 1
    return {
        "days": len(d),
        "hit_rate_2%": float((up >= target).mean()),
        "avg_range_%": float(((d["high"] - d["low"]) / d["open"]).mean() * 100),
        "avg_dollar_vol_M": float((d["close"] * d["volume"]).mean() / 1e6),
    }


def main(tickers: list[str]) -> None:
    import yfinance as yf

    rows = {}
    for tk in tickers:
        df = yf.download(tk, period="3mo", interval="1d", progress=False,
                         auto_adjust=True, multi_level_index=False)
        if not df.empty:
            rows[tk] = score(df)
    out = pd.DataFrame(rows).T.sort_values("hit_rate_2%", ascending=False)
    print(out.round(3).to_string())


if __name__ == "__main__":
    main(sys.argv[1:] or ["AAPL", "NVDA", "TSLA", "AMD", "PLTR", "SMCI"])
