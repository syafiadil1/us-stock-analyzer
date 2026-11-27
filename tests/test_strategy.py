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
    assert scanner.top(table, 3) == ["CCC", "AAA"]  # BBB illiquid
    assert scanner.top(table, 3, require_uptrend=True) == ["AAA"]
    msg = scanner.format_message(table, ["AAA"], 0.03)
    assert "AAA: hit 50%" in msg and "not a forecast" in msg


def test_journal_row_and_day_summary(tmp_path):
    from stock_analyzer.monitor import JOURNAL_COLS, day_summary, log_trade
    p = Params(slippage=0.0, take_profit=0, trail_drop=0, giveback=0.6, stop_loss=0.05)
    t = simulate_day(day([100, 101, 102, 103, 102.5, 101.5, 101.2, 101.0]), p)
    f = tmp_path / "j.csv"
    log_trade("NVDA", t, p, path=f)
    log_trade("TSLA", t, p, path=f)
    rows = pd.read_csv(f)
    assert list(rows.columns) == JOURNAL_COLS and len(rows) == 2
    assert rows.loc[0, "reason"] == "GIVEBACK" and abs(rows.loc[0, "ret_pct"] - 1.2) < 1e-6
    msg = day_summary({"NVDA": t})
    assert "NVDA: +1.20% (GIVEBACK" in msg and "Average: +1.20%" in msg
    assert "no trades" in day_summary({})


def test_scanner_split_handles_batch_and_single():
    from stock_analyzer.scanner import _split
    idx = pd.date_range("2026-11-02", periods=3, freq="B")
    one = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)
    batch = pd.concat({"AAA": one, "BBB": one * float("nan")}, axis=1)
    out = _split(batch, ["AAA", "BBB", "CCC"])
    assert list(out) == ["AAA"]  # BBB empty, CCC missing
    assert list(_split(one, ["ZZZ"])) == ["ZZZ"]


# ---------- breakout entry (RECOMMENDED preset) ----------
from stock_analyzer.strategy import (RECOMMENDED, breakout_entry, market_ok,  # noqa: E402
                                     opening_range_high, simulate_session)
from dataclasses import replace as _replace  # noqa: E402

BRK = _replace(RECOMMENDED, slippage=0.0)
OR_BARS = [100, 100.5, 101, 100.6, 100.2, 100.4, 100.8, 100.3, 100.1, 100.5, 100.7, 100.2, 100.4, 100.6, 100.5]


def qqq(green=True, n=40):
    step = 0.05 if green else -0.05
    return day([100 + step * (i + 1) for i in range(n)])


def test_market_filter_known_after_first_bars():
    assert market_ok(qqq(True), BRK) is True
    assert market_ok(qqq(False), BRK) is False
    assert market_ok(qqq(True, n=3), BRK) is None  # only 09:30-09:36 bars: not known yet


def test_breakout_buys_bar_after_close_above_opening_range():
    closes = OR_BARS + [101.5, 102, 103, 104, 103.5, 102.5, 102.2, 102.0]  # 10:15 bar closes above 101
    bars = day(closes)
    assert opening_range_high(bars, BRK) == 101
    assert breakout_entry(bars, BRK) == 16
    t = simulate_session(bars, BRK, index_bars=qqq(True))
    assert t is not None and t.entry == bars["open"].iloc[16] and t.reason == "GIVEBACK"


def test_no_trade_when_market_red_or_no_breakout_in_time():
    bars = day(OR_BARS + [101.5, 102, 103, 104])
    assert simulate_session(bars, BRK, index_bars=qqq(False)) is None
    late = day(OR_BARS + [100.5] * 17 + [102, 103])  # first close above 101 is the 11:06 bar
    assert breakout_entry(late, BRK) is None


def test_decide_breakout_states():
    from stock_analyzer.monitor import decide
    bars = day(OR_BARS + [101.5, 102, 103, 104, 103.5, 102.5, 102.2, 102.0])
    acts = [decide(bars.iloc[:n], BRK, index_bars=qqq(True))[0] for n in range(1, len(bars) + 1)]
    assert acts[:15] == ["WAIT"] * 15                 # opening range still forming
    assert acts[15] == "BUY"                          # 10:15 bar closed above the range high
    assert "SELL" in acts and acts.index("SELL") > 16
    assert decide(bars, BRK, index_bars=qqq(False))[0] == "SKIP"
    flat = day(OR_BARS + [100.5] * 20)
    assert decide(flat, BRK, index_bars=qqq(True))[0] == "SKIP"   # past 11:00, no breakout


def test_chop_rule_can_be_turned_off():
    closes = [100, 100.4, 99.9, 100.4, 99.9, 100.4, 99.9, 100.4, 99.9, 100.4, 99.9, 100.2]
    t = simulate_day(day(closes), Params(slippage=0.0, trail_drop=0.05, stop_loss=0.05, chop_flips=0))
    assert t.reason == "END_OF_DAY"


def test_session_close_early_days():
    import datetime as dt
    from stock_analyzer.monitor import session_close
    assert session_close(dt.date(2026, 11, 27)) == "13:00"   # day after Thanksgiving
    assert session_close(dt.date(2027, 11, 26)) == "13:00"
    assert session_close(dt.date(2026, 12, 24)) == "13:00"
    assert session_close(dt.date(2026, 11, 25)) == "16:00"


def test_gap_up_rule_skips_big_gap_ups():
    from stock_analyzer.monitor import decide
    bars = day(OR_BARS + [101.5, 102, 103, 104, 103.5, 102.5, 102.2, 102.0])  # opens at 100
    assert simulate_session(bars, BRK, index_bars=qqq(True), prev_close=96.0) is None   # +4.2% gap
    assert simulate_session(bars, BRK, index_bars=qqq(True), prev_close=98.0) is not None  # +2.0%
    assert simulate_session(bars, BRK, index_bars=qqq(True), prev_close=None) is not None  # unknown: trade
    action, detail = decide(bars, BRK, index_bars=qqq(True), prev_close=96.0)
    assert action == "SKIP" and "gapped up +4.2%" in detail


def test_live_uses_only_completed_bars():
    from stock_analyzer.monitor import complete_bars
    idx = pd.date_range("2026-11-24 09:30", "2026-11-24 09:40", freq="1min", tz="America/New_York")
    raw = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 10}, index=idx)
    ny = lambda hm: pd.Timestamp(f"2026-11-24 {hm}", tz="America/New_York")
    got = complete_bars(raw, ny("09:40:20"))       # 09:39 bar is still forming
    assert [t.strftime("%H:%M") for t in got.index] == ["09:30", "09:33", "09:36"]
    got = complete_bars(raw.iloc[:8], ny("09:39:05"))  # 09:38 minute not published yet
    assert [t.strftime("%H:%M") for t in got.index] == ["09:30", "09:33"]
    got = complete_bars(raw.iloc[:8], ny("09:41:30"))  # still missing after 2 extra minutes: accept
    assert [t.strftime("%H:%M") for t in got.index] == ["09:30", "09:33", "09:36"]


def test_archive_merges_and_dedupes(tmp_path):
    from stock_analyzer.archive import load, merge_save
    idx = pd.date_range("2026-11-24 09:30", periods=5, freq="5min", tz="America/New_York")
    a = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10}, index=idx)
    assert merge_save("ZZZ", "5m", a.iloc[:3], root=tmp_path) == 3
    b = a.iloc[2:].copy(); b["Close"] = 9.0            # overlaps one bar, newer value wins
    assert merge_save("ZZZ", "5m", b, root=tmp_path) == 5
    got = load("ZZZ", "5m", root=tmp_path)
    assert got.index[0] == idx[0] and got.loc[idx[2], "close"] == 9.0 and str(got.index.tz) == "America/New_York"


def test_review_wilson_and_verdicts():
    from stock_analyzer.review import review, wilson
    lo, hi = wilson(8, 10)
    assert 0.48 < lo < 0.50 and 0.93 < hi < 0.95
    row = lambda d, r: {"date": d, "ret_pct": r, "reason": "GIVEBACK", "settings": "entry=breakout sl=0.03"}
    few = pd.DataFrame([row("2026-11-30", 1.0), row("2026-12-01", -0.5)])
    assert "TOO EARLY" in review(few)
    days = [f"2026-12-{d:02d}" for d in range(1, 41 // 1) if d <= 28]
    bad = pd.DataFrame([row(days[i % len(days)], -1.5 if i % 4 else 0.5) for i in range(40)])
    assert "WORSE" in review(bad)
    old = pd.DataFrame([{"date": "2026-11-25", "ret_pct": 1.6, "reason": "CHOPPY", "settings": "tp=0.0 sl=0.03"}])
    out = review(old)
    assert "no trades with the recommended preset yet" in out and "1 older trades" in out


# ---------- hourly updates on bought stocks ----------
from types import SimpleNamespace  # noqa: E402

NY_TZ = "America/New_York"


def test_hourly_report_since_buy_then_last_hour_then_since_sell():
    from stock_analyzer.monitor import HourlyReport
    from stock_analyzer.strategy import Trade
    r = HourlyReport(60)
    t0 = pd.Timestamp("2026-11-24 10:18:15", tz=NY_TZ)
    r.bought("AAA", t0)
    r.update("AAA", 101.0, entry=100.0, line=97.0)
    assert not r.due(t0 + pd.Timedelta(minutes=30))
    m1 = r.message(pd.Timestamp("2026-11-24 11:00:15", tz=NY_TZ))
    assert m1.splitlines()[0] == "HOURLY UPDATE 11:00 NY (12:00 am Malaysia)"
    assert "since buy +1.00%" in m1 and "last hour" not in m1 and "sell if below 97.00" in m1
    r.update("AAA", 102.01)
    m2 = r.message(pd.Timestamp("2026-11-24 12:00:15", tz=NY_TZ))
    assert "last hour +1.00%" in m2 and "since buy +2.01%" in m2
    trade = SimpleNamespace(entry=100.0, exit=101.9, reason="GIVEBACK", ret=0.019)
    r.sold("AAA", trade, 102.0)
    r.update("AAA", 100.98)
    m3 = r.message(pd.Timestamp("2026-11-24 13:00:15", tz=NY_TZ))
    assert "AAA SOLD (GIVEBACK +1.90%): now 100.98" in m3 and "since sell -1.00%" in m3 and "last hour" not in m3
    r.update("AAA", None)
    assert "no price right now" in r.message(pd.Timestamp("2026-11-24 14:00:15", tz=NY_TZ))
    assert r.waiting(pd.Timestamp("2026-11-24 15:57", tz=NY_TZ))   # 15:00 still to come
    r.message(pd.Timestamp("2026-11-24 15:00:15", tz=NY_TZ))
    assert not r.waiting(pd.Timestamp("2026-11-24 15:57", tz=NY_TZ))
    off = HourlyReport(0)
    off.bought("AAA", t0)
    assert not off.pos and not off.due(t0 + pd.Timedelta(hours=2))
    assert Trade  # imported for the real type the monitor passes


def test_entry_point_and_sell_line_match_simulator():
    from stock_analyzer.monitor import entry_point, sell_line
    bars = day(OR_BARS + [101.5, 102, 103, 104])
    t_entry = bars["open"].iloc[16] * (1 + RECOMMENDED.slippage)
    assert entry_point(bars.iloc[:16], RECOMMENDED) == bars["close"].iloc[15] * (1 + RECOMMENDED.slippage)
    assert abs(entry_point(bars, RECOMMENDED) - t_entry) < 1e-9
    peak = 104.0
    gb = t_entry + (peak - t_entry) * (1 - RECOMMENDED.giveback)
    assert abs(sell_line(bars, RECOMMENDED) - gb) < 1e-9
    assert entry_point(bars.iloc[:10], RECOMMENDED) is None


def test_live_loop_sends_hourly_updates(monkeypatch):
    """Full live loop on a made-up day with a fake clock: BUY, hourly updates, SELL, more updates."""
    from stock_analyzer import monitor, notify
    closes = (OR_BARS + [101.5] + list(np.linspace(101.6, 105, 30)) + list(np.linspace(105, 102.5, 15)))
    closes += [103.0] * (130 - len(closes))
    stock, index = day(closes), qqq(True, n=130)
    clock = [pd.Timestamp("2026-11-24 09:29:00", tz=NY_TZ)]
    sent: list[str] = []

    def sleep(s):
        clock[0] += pd.Timedelta(seconds=s)
        assert clock[0] < pd.Timestamp("2026-11-24 17:00", tz=NY_TZ), "loop did not stop"

    def fetch(tk, now=None):
        b = index if tk == monitor.INDEX else stock
        return b[b.index + pd.Timedelta(minutes=3) <= clock[0]]

    monkeypatch.setattr(monitor, "_now", lambda: clock[0])
    monkeypatch.setattr(monitor, "time", SimpleNamespace(sleep=sleep, time=lambda: clock[0].timestamp()))
    monkeypatch.setattr(monitor, "fetch_today", fetch)
    monkeypatch.setattr(monitor, "prev_closes", lambda tickers: {})
    monkeypatch.setattr(monitor, "log_trade", lambda *a, **k: None)
    monkeypatch.setattr(notify, "send", lambda text: sent.append(text) or True)
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monitor.run_live(["AAA"], RECOMMENDED, max_positions=5, update_every=60)

    kinds = [m.split()[0] for m in sent]
    hourly = [m for m in sent if m.startswith("HOURLY")]
    assert kinds.index("BUY") < kinds.index("HOURLY") < kinds.index("SELL")
    assert [m.split()[2] for m in hourly] == ["11:00", "12:00", "13:00", "14:00", "15:00"]
    assert "since buy" in hourly[0] and "last hour" not in hourly[0]
    assert "last hour" in hourly[1] and "since buy" in hourly[1]
    assert "SOLD" in hourly[2] and "since sell" in hourly[2] and "last hour" not in hourly[2]
    assert "last hour +0.00%" in hourly[3]                    # flat after the sale
    assert sent[-1].startswith("Day summary") and "AAA: +" in sent[-1]
    assert clock[0] < pd.Timestamp("2026-11-24 15:10", tz=NY_TZ)   # stopped after the last update
