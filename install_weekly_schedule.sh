#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/run_research.sh"
LOG_FILE="$SCRIPT_DIR/logs/research_bot_weekly.log"
MARK="# DOCAI_TREND_RESEARCH_BOT_WEEKLY"

if [ ! -x "$RUN_SCRIPT" ]; then
  echo "run_research.sh not found or not executable: $RUN_SCRIPT"
  exit 1
fi

mkdir -p "$SCRIPT_DIR/logs"

if command -v launchctl >/dev/null 2>&1; then
  PLIST_FILE="$HOME/Library/LaunchAgents/com.genai.docai.trendbot.plist"
  mkdir -p "$(dirname "$PLIST_FILE")"

  cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.genai.docai.trendbot</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUN_SCRIPT</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>$LOG_FILE</string>
  <key>StandardErrorPath</key>
  <string>$LOG_FILE</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

  echo "created: $PLIST_FILE"

  launchctl bootout gui/$(id -u) "$PLIST_FILE" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$PLIST_FILE"
  echo "scheduled: launchd weekly every Monday 09:00"
  exit 0
fi

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
if echo "$CURRENT_CRON" | grep -q "DOCAI_TREND_RESEARCH_BOT_WEEKLY"; then
  echo "cron entry already exists"
  echo "$CURRENT_CRON"
  exit 0
fi

{
  echo "$CURRENT_CRON"
  echo "$MARK"
  echo "0 9 * * 1 /bin/bash $RUN_SCRIPT"
} | crontab -

echo "scheduled: cron weekly every Monday 09:00"
