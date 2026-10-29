#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK_PG_BIN="${PG_BIN:-}"
for task_candidate in /Library/PostgreSQL/16/bin /opt/homebrew/opt/postgresql@18/bin /opt/homebrew/opt/postgresql@17/bin /opt/homebrew/opt/postgresql@16/bin; do
    if [ -z "$TASK_PG_BIN" ] && [ -x "$task_candidate/pg_ctl" ]; then TASK_PG_BIN="$task_candidate"; fi
done
if [ -n "$TASK_PG_BIN" ] && [ -f "$TASK_ROOT/.local/postgres/PG_VERSION" ]; then
    if "$TASK_PG_BIN/pg_ctl" -D "$TASK_ROOT/.local/postgres" status >/dev/null 2>&1; then
        "$TASK_PG_BIN/pg_ctl" -D "$TASK_ROOT/.local/postgres" -m fast stop
    fi
fi
