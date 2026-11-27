#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu/Debian server. Run as your normal user:  bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt-get update -y && sudo apt-get install -y python3 python3-venv cron
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
if [ ! -f "$HOME/.stock_env" ]; then
  umask 077
  cat > "$HOME/.stock_env" <<'ENV'
export TELEGRAM_BOT_TOKEN="PASTE_NEW_TOKEN_HERE"
export TELEGRAM_CHAT_ID="PASTE_CHAT_ID_HERE"
ENV
  echo "Edit ~/.stock_env and fill in the token and chat id (nano ~/.stock_env)."
fi
REPO="$(pwd)"
LINE="CRON_TZ=America/New_York"
JOB="25 9 * * 1-5 cd $REPO && . \$HOME/.stock_env && .venv/bin/python -m stock_analyzer.monitor --preset recommended >> monitor.log 2>&1"
( crontab -l 2>/dev/null | grep -v -e 'stock_analyzer.monitor' -e '^CRON_TZ=' ; echo "$LINE"; echo "$JOB" ) | crontab -
echo "Done. Monitor will start at 09:25 New York time, Mon-Fri. Logs: $REPO/monitor.log"
