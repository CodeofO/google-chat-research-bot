#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/data/workspace/go.jung/SIDE_PJ/01_DOCAI_TREND"
INSTALL_SCRIPT="$BASE_DIR/install_weekly_schedule.sh"
RUN_SCRIPT="$BASE_DIR/run_research.sh"

usage() {
  cat <<EOF
Usage:
  1) bash $0 실행      -> 주간 스케줄 켜기(매주 월 09:00)
  2) bash $0 중단      -> 스케줄 중단
  3) bash $0 단일실행  -> 1회 즉시 실행

English aliases:
  start / stop / once, on / off / run
EOF
}

case "${1:-}" in
  1|실행|start|on)
    bash "$INSTALL_SCRIPT"
    ;;
  2|중단|stop|off|disable)
    if command -v launchctl >/dev/null 2>&1; then
      launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/com.genai.docai.trendbot.plist" 2>/dev/null || true
      launchctl remove com.genai.docai.trendbot 2>/dev/null || true
      rm -f "$HOME/Library/LaunchAgents/com.genai.docai.trendbot.plist"
      echo "disabled launchd: com.genai.docai.trendbot"
    fi

    if command -v crontab >/dev/null 2>&1; then
      crontab -l 2>/dev/null | grep -v DOCAI_TREND_RESEARCH_BOT_WEEKLY | crontab -
      echo "disabled cron schedule"
    fi
    ;;
  3|단일실행|단일|once|run)
    bash "$RUN_SCRIPT"
    ;;
  *)
    usage
    exit 1
    ;;
esac
