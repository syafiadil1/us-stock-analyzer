#!/usr/bin/env bash
# macOS: start the monitor automatically every weekday, no terminal or Claude app needed.
#   bash deploy/mac_autostart.sh            # install (or update) the schedule
#   bash deploy/mac_autostart.sh --now      # install and also start it right now
#   bash deploy/mac_autostart.sh --status   # is it installed / running? last log lines
#   bash deploy/mac_autostart.sh --remove   # uninstall
#
# Runs `deploy/mac_run.sh` (recommended preset) Mon-Fri at START_HOUR:START_MIN Mac time.
# 21:00 Malaysia time is before the 09:20 New York scan in both US summer and winter time;
# the monitor itself waits for the right New York time. Output goes to monitor.log.
# The Mac must be ON and AWAKE at that time (plugged in, lid open). If it is asleep,
# macOS runs the job as soon as it wakes. To wake it automatically (needs your password):
#   sudo pmset repeat wakeorpoweron MTWRF 20:55:00
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.stockanalyzer.monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
START_HOUR="${START_HOUR:-21}"
START_MIN="${START_MIN:-0}"

case "${1:-}" in
  --remove)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Auto-start removed. (A monitor that is running now keeps running until the close.)"
    exit 0 ;;
  --status)
    if [ -f "$PLIST" ]; then echo "Installed: $PLIST"; else echo "Not installed"; fi
    pgrep -fl "[p]ython[0-9.]* -m stock_analyzer[.]monitor" || echo "Monitor not running right now"
    [ -f "$REPO/monitor.log" ] && { echo "--- last log lines:"; tail -5 "$REPO/monitor.log"; }
    exit 0 ;;
esac

mkdir -p "$HOME/Library/LaunchAgents"
{
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$REPO/deploy/mac_run.sh</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
EOF
  for d in 1 2 3 4 5; do
    echo "    <dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>$START_HOUR</integer><key>Minute</key><integer>$START_MIN</integer></dict>"
  done
  cat <<EOF
  </array>
  <key>StandardOutPath</key><string>$REPO/monitor.log</string>
  <key>StandardErrorPath</key><string>$REPO/monitor.log</string>
</dict>
</plist>
EOF
} > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Auto-start installed: Mon-Fri $(printf '%02d:%02d' "$START_HOUR" "$START_MIN") Mac time. Log: $REPO/monitor.log"
if [ "${1:-}" = "--now" ]; then
  launchctl kickstart "$DOMAIN/$LABEL"
  echo "Started now."
fi
