"""How good is the system REALLY? Live paper trades vs the backtest, with honest error bars.

    python -m stock_analyzer.review               # read paper_trades.csv
    python -m stock_analyzer.review --telegram    # also send the summary to Telegram

Win-rate ranges use the Wilson interval; average-return ranges resample whole DAYS
(trades on the same day move together, so counting them as independent would overstate
how sure we are).
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

# Backtest of the recommended preset (60 days of 5-min data, Sep 2 - Nov 25 2026,
# first 5 breakouts per day, costs 0.1%/side). Ranges = 95%, resampling days.
BACKTEST = dict(trades=69, days=25, win=0.768, win_lo=0.64, win_hi=0.87,
                avg=0.0073, avg_lo=-0.0006, avg_hi=0.0143, sd=0.0232)
MIN_TRADES = 30  # below this the live numbers say very little


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def day_bootstrap(rets: pd.Series, days: pd.Series, n: int = 4000, seed: int = 0):
    """95% range of the average return, plus the chance the true average is <= 0."""
    by = [g.values for _, g in rets.groupby(days.values)]
    if len(by) < 2:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.array([np.concatenate([by[i] for i in rng.integers(0, len(by), len(by))]).mean()
                      for _ in range(n)])
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)), float((means <= 0).mean())


def review(df: pd.DataFrame) -> str:
    live = df[df["settings"].astype(str).str.contains("entry=breakout")] if "settings" in df else df.iloc[0:0]
    other = len(df) - len(live)
    b = BACKTEST
    lines = ["PAPER TRADING REVIEW (recommended preset)",
             f"Backtest: {b['win'] * 100:.0f}% wins (likely {b['win_lo'] * 100:.0f}-{b['win_hi'] * 100:.0f}%), "
             f"avg {b['avg'] * 100:+.2f}% per trade (likely {b['avg_lo'] * 100:+.2f} to {b['avg_hi'] * 100:+.2f}%)"]
    n = len(live)
    if n == 0:
        lines.append("Live: no trades with the recommended preset yet.")
    else:
        r = live["ret_pct"].astype(float) / 100
        wins = int((r > 0).sum())
        lo, hi = wilson(wins, n)
        alo, ahi, p_neg = day_bootstrap(r, live["date"])
        lines.append(f"Live: {n} trades on {live['date'].nunique()} days | wins {wins / n * 100:.0f}% "
                     f"(likely {lo * 100:.0f}-{hi * 100:.0f}%) | avg {r.mean() * 100:+.2f}% per trade"
                     + ("" if math.isnan(alo) else f" (likely {alo * 100:+.2f} to {ahi * 100:+.2f}%)"))
        lines.append(f"Total if each trade had the same size: {r.sum() * 100:+.1f}% | best {r.max() * 100:+.2f}% "
                     f"| worst {r.min() * 100:+.2f}% | exits: {live['reason'].value_counts().to_dict()}")
        if n < MIN_TRADES:
            weeks = math.ceil((MIN_TRADES - n) / 5.8)
            lines.append(f"Verdict: TOO EARLY. {n} trades is not enough to judge; need about {MIN_TRADES} "
                         f"(~{weeks} more weeks at ~6 trades/week).")
        elif hi < b["win_lo"] or (not math.isnan(ahi) and ahi < b["avg_lo"]):
            lines.append("Verdict: WORSE than the backtest range. The edge may not be real: do not use real money.")
        elif not math.isnan(p_neg) and p_neg > 0.2:
            lines.append(f"Verdict: UNCLEAR. Live results fit the backtest, but there is a {p_neg * 100:.0f}% chance "
                         "the true average is zero or negative. Keep paper trading.")
        else:
            lines.append("Verdict: CONSISTENT with the backtest so far (still not a guarantee).")
    need = int((1.96 * b["sd"] / 0.003) ** 2)
    lines.append(f"For +/-0.3% precision on the average you need about {need} trades (~{need / 5.8:.0f} weeks).")
    if other:
        lines.append(f"({other} older trades from other settings were left out.)")
    lines.append("Paper results, not advice. Past results do not guarantee future ones.")
    return "\n".join(lines)


def main() -> None:
    from . import notify
    from .monitor import JOURNAL

    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(JOURNAL))
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    f = Path(a.file)
    df = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["date", "ret_pct", "reason", "settings"])
    msg = review(df)
    print(msg)
    if a.telegram:
        print("Telegram sent:", notify.send(msg))


if __name__ == "__main__":
    main()
