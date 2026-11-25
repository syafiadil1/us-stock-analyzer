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


def test_buy_signal_fires_once_at_entry_bar():
    from stock_analyzer.monitor import decide
    bars = day([100, 100.1, 100.0, 100.05])
    p = Params(slippage=0.0, entry_delay=1)
    acts = [decide(bars.iloc[:n], p)[0] for n in range(1, 5)]
    assert acts == ["WAIT", "BUY", "HOLD", "HOLD"]


def test_notify_is_noop_without_env(monkeypatch):
    from stock_analyzer import notify
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.send("hi") is False


def test_notify_posts_and_hides_token_on_failure(monkeypatch, capsys):
    from stock_analyzer import notify
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: calls.append(req) or R())
    assert notify.send("BUY NVDA") is True and "SECRET123" in calls[0].full_url

    def boom(req, timeout): raise OSError(req.full_url)
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send("x") is False
    assert "SECRET123" not in capsys.readouterr().out


def test_giveback_example_from_readme():
    # entry 100, peak 103 (+3%), 60% give-back -> sell at or below 101.20
    p = Params(slippage=0.0, take_profit=0, trail_drop=0, giveback=0.6, stop_loss=0.05)
    t = simulate_day(day([100, 101, 102, 103, 102.5, 101.5, 101.2, 101.0]), p)
    assert t.reason == "GIVEBACK" and abs(t.exit - 101.2) < 1e-9


def test_giveback_waits_until_armed():
    # only up 0.3% (< 0.5% arm), then dips: give-back must not fire
    p = Params(slippage=0.0, take_profit=0, trail_drop=0, giveback=0.6, stop_loss=0.05)
    t = simulate_day(day([100, 100.3, 100.05, 100.0, 100.1]), p)
    assert t.reason == "END_OF_DAY"


def test_giveback_off_by_default_keeps_old_behaviour():
    closes = [100, 100.5, 101, 101.5, 101.9, 101.1, 101.0, 101.0]
    assert simulate_day(day(closes), NOSLIP).reason == "TRAILING_DROP"


def test_hold_shows_giveback_sell_price():
    from stock_analyzer.monitor import decide
    p = Params(slippage=0.0, take_profit=0, trail_drop=0, giveback=0.6, stop_loss=0.05)
    action, detail = decide(day([100, 101, 103, 102.8]), p, final=False)
    assert action == "HOLD" and "sell if below 101.20" in detail


def test_scanner_score_and_top():
    from stock_analyzer import scanner
    idx = pd.date_range("2026-08-01", periods=30, freq="B")
    up = pd.DataFrame({"Open": 100.0, "High": 104.0, "Low": 99.0,
                       "Close": np.linspace(100, 110, 30), "Volume": 5e6}, index=idx)
    s = scanner.score(up, target=0.03)
    assert s["hit_rate"] == 1.0 and s["trend_up"] and s["dollar_vol_M"] > 200
    table = pd.DataFrame({
        "AAA": {"hit_rate": 0.5, "dollar_vol_M": 900.0, "trend_up": True, "avg_range_%": 4.0, "premkt_%": 1.0},
        "BBB": {"hit_rate": 0.9, "dollar_vol_M": 50.0, "trend_up": True, "avg_range_%": 6.0, "premkt_%": None},
        "CCC": {"hit_rate": 0.8, "dollar_vol_M": 900.0, "trend_up": False, "avg_range_%": 5.0, "premkt_%": None},
    }).T
    assert scanner.top(table, 3) == ["AAA"]  # BBB illiquid, CCC downtrend
    msg = scanner.format_message(table, ["AAA"], 0.03)
    assert "AAA: hit 50%" in msg and "not a forecast" in msg
