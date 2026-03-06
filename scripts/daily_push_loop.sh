#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
CONFIG_PATH=${DAILY_PAPER_PUSH_CONFIG:-}
RUN_SCRIPT="$SCRIPT_DIR/run_daily_push.sh"
LOG_FILE="$REPO_ROOT/logs/daily_push_loop.log"

if [[ -z "$CONFIG_PATH" ]]; then
  if [[ -f "$REPO_ROOT/config/local.yaml" ]]; then
    CONFIG_PATH="$REPO_ROOT/config/local.yaml"
  else
    CONFIG_PATH="$REPO_ROOT/config/default.yaml"
  fi
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  RESOLVED_PYTHON_BIN="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  RESOLVED_PYTHON_BIN=$(command -v python3)
elif [[ -x "$HOME/miniconda3/envs/paper/bin/python" ]]; then
  RESOLVED_PYTHON_BIN="$HOME/miniconda3/envs/paper/bin/python"
else
  echo "python3 executable not found. Set PYTHON_BIN explicitly." >&2
  exit 1
fi

mkdir -p "$REPO_ROOT/logs"

schedule_info=$(
  cd "$REPO_ROOT" && \
  CONFIG_PATH="$CONFIG_PATH" "$RESOLVED_PYTHON_BIN" -c 'import os; from src.scheduler.daily_job import load_default_config; cfg = load_default_config(os.environ["CONFIG_PATH"]); scheduler = cfg.get("scheduler", {}); print(str(scheduler.get("daily_time", "09:00")) + "|" + str(scheduler.get("timezone", "Asia/Shanghai")))'
)

TARGET_TIME=${schedule_info%%|*}
TZ_NAME=${schedule_info##*|}
TARGET_HOUR=${TARGET_TIME%%:*}
TARGET_MINUTE=${TARGET_TIME##*:}

while true; do
  now_epoch=$(TZ="$TZ_NAME" date +%s)
  today=$(TZ="$TZ_NAME" date +%F)
  target_epoch=$(TZ="$TZ_NAME" date -d "$today ${TARGET_HOUR}:${TARGET_MINUTE}:00" +%s)

  if [ "$now_epoch" -ge "$target_epoch" ]; then
    target_epoch=$(TZ="$TZ_NAME" date -d "tomorrow ${TARGET_HOUR}:${TARGET_MINUTE}:00" +%s)
  fi

  sleep_sec=$((target_epoch - now_epoch))
  next_run=$(TZ="$TZ_NAME" date -d "@$target_epoch" '+%F %T %Z')
  echo "[$(date '+%F %T %Z')] next run at $next_run (sleep ${sleep_sec}s)" >> "$LOG_FILE"
  sleep "$sleep_sec"

  echo "[$(date '+%F %T %Z')] triggering daily push" >> "$LOG_FILE"
  if "$RUN_SCRIPT"; then
    echo "[$(date '+%F %T %Z')] daily push finished" >> "$LOG_FILE"
  else
    echo "[$(date '+%F %T %Z')] daily push failed" >> "$LOG_FILE"
  fi

done
