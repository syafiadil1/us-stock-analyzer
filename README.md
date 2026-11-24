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
