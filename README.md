# us-stock-analyzer

Backtest a simple intraday routine on US stocks: buy at the open, watch every
3 minutes, sell by rules (take profit, choppy, trailing drop, stop loss).

## Run (on your own machine, needs internet to Yahoo Finance)

    pip install -r requirements.txt
    python -m pytest -q
    python -m stock_analyzer.scanner NVDA TSLA AMD PLTR      # which stocks often hit +2%
    python -m stock_analyzer.backtest --tickers NVDA TSLA AMD --days 28

## Rules (tune in `stock_analyzer/strategy.py`, `Params`)

| Rule | Default |
|---|---|
| Take profit | +2% from entry |
| Stop loss | -1% from entry (assumption, not in the original routine) |
| Trailing drop | sell if price falls 0.7% from running high |
| Choppy | 4+ fast direction flips (>=0.15% each) in the last 8 bars |
| End of day | sell at the last bar, no overnight |

Market opens 09:30 New York time = 10:30pm Malaysia (winter/EST) or 9:30pm (summer/EDT).

**Caveats:** Yahoo only keeps ~30 days of 1-minute data, so results are a small
sample. Backtests ignore real fills and spreads. Not financial advice.

## Paper-trade monitor (alerts only, no orders)

    python -m stock_analyzer.monitor NVDA TSLA AMD            # live, start before the open
    python -m stock_analyzer.monitor NVDA --replay 2026-11-20 # replay a past day

Checks every 3 minutes with the same rules as the backtest (default: wait 2 bars,
stop 1.5%, trail 1.5%). Closed trades are appended to `paper_trades.csv`.
BUY = enter now (shows take-profit and stop prices), HOLD = stay in, SELL = exit, WAIT = entry delay not over yet.
Run it 2-4 weeks, then compare `paper_trades.csv` with the backtest before using real money.

### Telegram alerts (optional)

1. In Telegram, talk to @BotFather -> `/newbot` -> copy the token. Send any message to your new bot.
2. Get your chat id (e.g. message @userinfobot).
3. Before running the monitor:

       export TELEGRAM_BOT_TOKEN="123456:ABC..."
       export TELEGRAM_CHAT_ID="123456789"

You then get a phone message on every BUY and SELL. Never commit the token.

## Run it on a cloud server (no laptop/phone needed)

On a small Ubuntu server: `git clone <repo> && cd us-stock-analyzer && bash deploy/setup.sh`,
then `nano ~/.stock_env` to put in your Telegram token and chat id (the file is private to your user).
Cron starts the monitor at 09:25 New York time every weekday; edit the tickers in `deploy/setup.sh` if you want others.

## Run on a Mac

    bash deploy/mac_run.sh                 # recommended setup (below)
    bash deploy/mac_run.sh NVDA TSLA AMD   # your own list, older rules

Uses `caffeinate` so the Mac does not sleep while the monitor runs. Keep it plugged in.
It refuses to start a second copy, so you never get duplicate alerts.

### Auto-start every weekday (no terminal or Claude app needed)

    bash deploy/mac_autostart.sh            # install: Mon-Fri 21:00 Mac time
    bash deploy/mac_autostart.sh --status   # installed? running? last log lines
    bash deploy/mac_autostart.sh --remove   # uninstall

21:00 Malaysia time is before the 09:20 New York scan in both US summer and winter time.
Output goes to `monitor.log`. The Mac must be on and awake then (plugged in, lid open); if it
is asleep, macOS starts the job when it wakes. To wake it automatically:
`sudo pmset repeat wakeorpoweron MTWRF 20:55:00`.

## Scanner + give-back sell alert

**Scanner**: which stocks have OFTEN risen X% after the open (last 3 months), filtered to
liquid stocks with a hit rate of at least 20%, plus their pre-market move. On 2 years of daily
data a high past hit rate did predict more +3% days (42% vs 7%), but the average open-to-close
return was still ~0: it finds stocks that move, not stocks that will rise. An uptrend filter
was tested and did not help, so it is off.

    .venv/bin/python -m stock_analyzer.scanner --target 0.03 --top 5 --telegram

**Give-back sell**: once a trade is up at least 0.5%, SELL when it has handed back a share of its
best gain. With `--giveback 0.6`: entry 100, peak 103 (+3%) -> sell at 101.20. Any share works
(0.4, 0.5, ...). `--take-profit 0 --trail-drop 0` turns off the fixed exits so winners can run.

Let the scanner pick 5 stocks at 09:20 NY, then watch them with a 50% give-back:

    bash deploy/mac_run.sh --scan 5 --scan-target 0.03 --giveback 0.5 --take-profit 0 --trail-drop 0

The hit rate is history, not a forecast. Backtest a give-back level before trusting it.

Each closed paper trade goes to `paper_trades.csv` (entry, exit, reason, return, best gain,
settings), and a day summary is sent to Telegram at the end. Data errors are retried
on the next check; you get a Telegram warning if a stock keeps failing or the monitor crashes.

**Hourly updates.** Once a stock gets a BUY, one Telegram message every clock hour
(11:00, 12:00 ... 15:00 NY = midnight to 4 am Malaysia in US winter) lists each stock bought today:

    HOURLY UPDATE 12:00 NY (1:00 am Malaysia)
    ADBE holding: now 251.07 | last hour +0.38% | since buy +0.42% (in at ~250.02) | sell if below 242.52
    PATH SOLD (GIVEBACK +0.44%): now 13.40 | since sell +0.94%

Sold stocks stay in the update until the close, so you can see whether selling was right.
The first update after a buy (or sale) shows only "since buy" ("since sell"). `--update-every 30`
changes the interval, `--update-every 0` turns it off. The updates are information only and
do not change any buy/sell rule.

## Recommended setup (what the tests showed)

`bash deploy/mac_run.sh` (= `--preset recommended`):

1. 09:20 NY: the scanner sends the 15 liquid stocks that most often rose 3%+ after the open.
2. 09:39 NY: if QQQ is below its open, **no trades today** (you get one SKIP message).
3. Skip any stock that opened more than 3% above yesterday's close (big gap-ups tend to fade).
4. Otherwise wait for each stock to close above its 09:30-10:15 high. First 5 that do so
   before 11:00 get a BUY alert. Stocks that never break out are skipped.
5. Exit: stop 3%; once up 0.5%, sell after giving back 40% of the best gain; else at the close.
   No fixed take-profit and no "choppy" exit (both lowered results in the test).

Tested on Yahoo data, Sep 2 - Nov 25 2026, ~100 stocks, top-15 candidates per day:

| Setup | Trades | Win rate | Avg per trade | 1st / 2nd half |
|---|---|---|---|---|
| Old: buy 6 min after open, top 5 | 300 | 48% | -0.20% | -0.40 / -0.01 |
| Breakout without the QQQ filter | 252 | 60% | -0.04% | -0.25 / +0.16 |
| Breakout + QQQ, first 5 per day | 77 | 73% | +0.64% | +0.27 / +0.88 |
| **Recommended (+ skip gap-ups > 3%, costs 0.1%/side)** | 69 | **77%** | **+0.73%** | +0.28 / +1.02 |
| Same with costs 0.2%/side (pessimistic) | 69 | 71% | +0.55% | +0.30 / +0.71 |
| Same, without its 3 best days | - | 68% | +0.06% | - |
| Same rules on 3-min bars (19 days, overlaps) | 41 | 83% | +0.44% | +0.26 / +1.15 |

The gap rule was chosen on the first 30 days only and then improved the last 30 days too; it
also matches 2 years of daily data (gap-ups > 3% averaged -0.28% open-to-close).

Nov 27 2026, 20 more ideas were tested against this preset with pass rules fixed in advance
(better average AND win rate, better in all three 20-day blocks, still better at 0.2% costs,
no lower total, >= 45 trades, >= 80% chance of being better when days are resampled).
None passed: ranking by volatility, a bigger pool, time stops at 14:00/15:00, skipping
over-extended or "chasing" breakouts, stop 2.5%, give-back 35/45%, entry deadline
10:45/11:30, gap limit 2%, skipping stocks under $10 (closest: 5 of 7 checks), two-close
confirmation, skipping correlated stocks (hurt: profits come in clusters of related stocks
moving together, which is also a risk), stricter hit rate, max 3 or 8 positions per day
(8 earns more in total, not more per trade). So the preset stays as it is.

What did NOT help: stock-vs-QQQ strength, QQQ still green at the breakout, breakout-bar
volume, opening-range width, a fixed take-profit (win rate unchanged, return lower), the uptrend
filter, volatility-scaled stops, relative-volume and gap filters, a 13-feature model.

Limits, read these: 60 days is still a small sample and covers one market period.
How sure are we? Resampling the 25 trading days: win rate 77% (likely 64-87%), average
+0.73% per trade (likely -0.06% to +1.43%). That range still includes zero.
Yahoo has no older 5-minute data. A coarser 1-hour version of the same idea over 2 years
was only break-even (52% wins, -0.02%), and it also lost on these same 60 days (-0.36%),
so the edge depends on the 5-minute details and may be fragile: only live paper trading
can confirm it. The
result leans on strong market days (without the best 3 days it is close to break-even),
trades happen on only about half the days, and some days lose 3% (stop-outs). Live
fills will differ from the backtest. Paper-trade it for several weeks first.

## How accurate is it really? (review + data archive)

    .venv/bin/python -m stock_analyzer.review          # live paper trades vs backtest, with ranges
    .venv/bin/python -m stock_analyzer.archive --info  # how much intraday data is stored

- `review` gives win rate and average with 95% ranges and a verdict (TOO EARLY / WORSE /
  UNCLEAR / CONSISTENT). It needs ~30 trades (~5 weeks) to say anything, and ~230 trades
  (~9 months) to pin the average down to +/-0.3%. `mac_run.sh` sends it to Telegram on Fridays.
- `archive` keeps the 1-min and 5-min bars Yahoo deletes after 30/60 days
  (`data/archive/`, not in git). `mac_run.sh` adds each day after the close. More stored
  days = backtests that can actually be trusted; re-test the rules on them every few months.
- The live monitor only acts on finished 3-minute bars (Yahoo also sends the minute still
  in progress), so live alerts follow exactly the rules that were backtested.
