import numpy as np
import pandas as pd

from stock_analyzer.backtest import summarize
from stock_analyzer.data import to_3min
from stock_analyzer.strategy import Params, simulate_day

NOSLIP = Params(slippage=0.0)


def day(closes, start=100.0):
    idx = pd.date_range("2026-11-24 09:30", periods=len(closes), freq="3min", tz="America/New_York")
    opens = [start] + list(closes[:-1])
    return pd.DataFrame({"open": opens, "high": closes, "low": closes, "close": closes}, index=idx)


def test_take_profit_on_steady_climb():
    t = simulate_day(day(list(np.linspace(100, 103, 30))), NOSLIP)
    assert t.reason == "TAKE_PROFIT" and t.ret >= 0.02


def test_trailing_drop_after_run_up():
    closes = [100, 100.5, 101, 101.5, 101.9, 101.1, 101.0, 101.0]  # up 1.9% then falls 0.8%
    t = simulate_day(day(closes), NOSLIP)
    assert t.reason == "TRAILING_DROP"


def test_stop_loss():
    t = simulate_day(day([100, 99.6, 99.2, 98.9, 98.8]), NOSLIP)
    assert t.reason == "STOP_LOSS"


def test_choppy_exit():
    closes = [100, 100.4, 99.9, 100.4, 99.9, 100.4, 99.9, 100.4, 99.9, 100.4, 99.9, 100.2]
    t = simulate_day(day(closes), Params(slippage=0.0, trail_drop=0.05, stop_loss=0.05))
    assert t.reason == "CHOPPY"


def test_end_of_day_when_nothing_happens():
    t = simulate_day(day([100, 100.1, 100.0, 100.05, 100.0, 100.02]), NOSLIP)
    assert t.reason == "END_OF_DAY"


def test_slippage_reduces_return():
    closes = list(np.linspace(100, 103, 30))
    assert simulate_day(day(closes), Params()).ret < simulate_day(day(closes), NOSLIP).ret


def test_resample_to_3min_regular_hours_only():
    idx = pd.date_range("2026-11-24 09:00", "2026-11-24 16:30", freq="1min", tz="America/New_York")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 10}, index=idx)
    out = to_3min(df)
    assert out.index[0].strftime("%H:%M") == "09:30" and out.index[-1].strftime("%H:%M") == "15:57"
    assert len(out) == 130 and out["volume"].iloc[0] == 30


def test_summarize():
    t = simulate_day(day(list(np.linspace(100, 103, 30))), NOSLIP)
    s = summarize([t, t])
    assert s["trades"] == 2 and s["win_rate"] == 1.0


def test_live_mode_does_not_force_end_of_day():
    from stock_analyzer.monitor import decide
    bars = day([100, 100.1, 100.0, 100.05, 100.0, 100.02])
    assert decide(bars, NOSLIP, final=False)[0] == "HOLD"
    assert decide(bars, NOSLIP, final=True)[0] == "SELL"


def test_live_decision_matches_backtest_when_replayed():
    from stock_analyzer.monitor import decide
    closes = list(np.linspace(100, 103, 30))
    bars = day(closes)
    expected = simulate_day(bars, NOSLIP)
    for n in range(2, len(bars) + 1):
        action, detail = decide(bars.iloc[:n], NOSLIP, final=(n == len(bars)))
        if action == "SELL":
            assert expected.reason in detail
            break
    else:
        raise AssertionError("never sold")
