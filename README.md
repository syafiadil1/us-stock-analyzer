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
