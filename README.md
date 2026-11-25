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

    bash deploy/mac_run.sh NVDA TSLA AMD

Uses `caffeinate` so the Mac does not sleep while the monitor runs. Keep it plugged in.

## Scanner + give-back sell alert

**Scanner**: which stocks have OFTEN risen X% after the open (last 3 months), filtered to
liquid stocks in an uptrend with a hit rate of at least 20%, plus their pre-market move:

    .venv/bin/python -m stock_analyzer.scanner --target 0.03 --top 5 --telegram

**Give-back sell**: once a trade is up at least 0.5%, SELL when it has handed back a share of its
best gain. With `--giveback 0.6`: entry 100, peak 103 (+3%) -> sell at 101.20. Any share works
(0.4, 0.5, ...). `--take-profit 0 --trail-drop 0` turns off the fixed exits so winners can run.

Let the scanner pick 5 stocks at 09:20 NY, then watch them with a 50% give-back:

    bash deploy/mac_run.sh --scan 5 --scan-target 0.03 --giveback 0.5 --take-profit 0 --trail-drop 0

The hit rate is history, not a forecast. Backtest a give-back level before trusting it.
