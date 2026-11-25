#!/usr/bin/env bash
# macOS: set up once, then run the monitor with the Mac kept awake.
#   bash deploy/mac_run.sh NVDA TSLA AMD
# Telegram (optional): put the two export lines in ~/.stock_env (chmod 600), see README.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "Install Python first: https://www.python.org/downloads/ or 'brew install python'"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f "$HOME/.stock_env" ] && . "$HOME/.stock_env"
TICKERS=("$@"); [ ${#TICKERS[@]} -gt 0 ] || TICKERS=(NVDA TSLA AMD)
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  .venv/bin/python -c "from stock_analyzer import notify; print('Telegram test sent:', notify.send('Mac monitor ready'))"
else
  echo "No Telegram token set: alerts will only show in this window."
fi
echo "Starting monitor for ${TICKERS[*]} (Ctrl+C to stop). Keep the Mac plugged in."
exec caffeinate -dims .venv/bin/python -m stock_analyzer.monitor "${TICKERS[@]}"
