#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
if [ ! -x "$TASK_ROOT/.venv/bin/python" ]; then
    echo 'Run scripts/setup-local.sh first.'
    exit 1
fi
exec "$TASK_ROOT/.venv/bin/python" "$TASK_ROOT/evals/engineering/run.py"
