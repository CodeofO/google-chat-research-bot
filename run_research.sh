#!/usr/bin/env bash
set -euo pipefail

REPO=/data/workspace/go.jung/SIDE_PJ/01_DOCAI_TREND
VENV=/data/workspace/go.jung/.venv-pj-trend
ENV_FILE="$REPO/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

: "${GOOGLE_CHAT_WEBHOOK_URL:?GOOGLE_CHAT_WEBHOOK_URL 환경변수를 export 하거나 .env에 설정해야 합니다.}"

export LOCAL_LLM_BASE_URL="${LOCAL_LLM_BASE_URL:-${BASE_URL:-http://localhost:8000/v1}}"
export LOCAL_LLM_API_KEY="${LOCAL_LLM_API_KEY:-${API_KEY:-local-no-key-required}}"
export LOCAL_LLM_MODEL="${LOCAL_LLM_MODEL:-${MODEL_NAME:-n-mix}}"

cd "$REPO"
source "$VENV/bin/activate"
mkdir -p "$REPO/reports"

python main.py \
  --output "$REPO/reports/$(date +%F).md"
