#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
CONFIG_PATH=${DAILY_PAPER_PUSH_CONFIG:-}
CONDA_ENV_NAME=${CONDA_ENV_NAME:-paper}

if [[ -z "$CONFIG_PATH" ]]; then
  if [[ -f "$REPO_ROOT/config/local.yaml" ]]; then
    CONFIG_PATH="$REPO_ROOT/config/local.yaml"
  else
    CONFIG_PATH="$REPO_ROOT/config/default.yaml"
  fi
fi

if [[ -n "${CONDA_BIN:-}" ]]; then
  RESOLVED_CONDA_BIN="$CONDA_BIN"
elif command -v conda >/dev/null 2>&1; then
  RESOLVED_CONDA_BIN=$(command -v conda)
elif [[ -x "$HOME/miniconda3/bin/conda" ]]; then
  RESOLVED_CONDA_BIN="$HOME/miniconda3/bin/conda"
else
  echo "conda executable not found. Set CONDA_BIN or add conda to PATH." >&2
  exit 1
fi

mkdir -p "$REPO_ROOT/logs"
cd "$REPO_ROOT"
"$RESOLVED_CONDA_BIN" run -n "$CONDA_ENV_NAME" python -m src.scheduler.daily_job --config "$CONFIG_PATH" --run-live-today >> "$REPO_ROOT/logs/daily_push.log" 2>&1
