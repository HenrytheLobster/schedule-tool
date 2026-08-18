#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

python3 "$SCRIPT_DIR/scheduler.py"

LATEST_QUEUE="$(ls -t "$SCRIPT_DIR"/queue_*.json 2>/dev/null | head -n 1 || true)"

if [[ -z "${LATEST_QUEUE}" ]]; then
  echo "No queue file found after scheduler run."
  exit 1
fi

python3 "$SCRIPT_DIR/queue_runner.py" "$LATEST_QUEUE"
