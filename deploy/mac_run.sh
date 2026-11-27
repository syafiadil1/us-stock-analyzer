#!/usr/bin/env bash
# macOS: set up once, then run the monitor with the Mac kept awake.
#   bash deploy/mac_run.sh                # recommended: scanner + breakout entry (tested setup)
#   bash deploy/mac_run.sh NVDA TSLA AMD  # or watch your own list with the older rules
# Telegram (optional): put the two export lines in ~/.stock_env (chmod 600), see README.
set -euo pipefail
cd "$(dirname "$0")/.."
if pgrep -f "[p]ython[0-9.]* -m stock_analyzer[.]monitor" >/dev/null; then
  echo "$(date '+%F %T') A monitor is already running; not starting a second one (no duplicate alerts)."
  exit 0
fi
command -v python3 >/dev/null || { echo "Install Python first: https://www.python.org/downloads/ or 'brew install python'"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f "$HOME/.stock_env" ] && . "$HOME/.stock_env"
TICKERS=("$@")
# Default = settings that held up best in the backtest (see README "Recommended setup").
[ ${#TICKERS[@]} -gt 0 ] || TICKERS=(--preset recommended)
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  .venv/bin/python -c "from stock_analyzer import notify; print('Telegram test sent:', notify.send('Mac monitor ready'))"
else
  echo "No Telegram token set: alerts will only show in this window."
fi
echo "Starting monitor: ${TICKERS[*]} (Ctrl+C to stop). Keep the Mac plugged in."
caffeinate -dims .venv/bin/python -m stock_analyzer.monitor "${TICKERS[@]}" || echo "monitor exited with an error"
# After the session: keep today's bars before Yahoo deletes them (for future, larger backtests).
echo "$(date '+%F %T') saving today's data to data/archive..."
.venv/bin/python -m stock_analyzer.archive 2>&1 | grep -v -i -E "warning|delisted|failed download" || true
# Fridays (New York): send the weekly paper-trading review with honest error bars.
if [ "$(TZ=America/New_York date +%u)" = "5" ] && [ ${#TICKERS[@]} -gt 0 ] && [ "${TICKERS[0]}" = "--preset" ]; then
  .venv/bin/python -m stock_analyzer.review --telegram || true
fi
