#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
TASK_PG_BIN="${PG_BIN:-}"
for task_candidate in /Library/PostgreSQL/16/bin /opt/homebrew/opt/postgresql@18/bin /opt/homebrew/opt/postgresql@17/bin /opt/homebrew/opt/postgresql@16/bin; do
    if [ -z "$TASK_PG_BIN" ] && [ -x "$task_candidate/initdb" ]; then TASK_PG_BIN="$task_candidate"; fi
done
if [ -z "$TASK_PG_BIN" ]; then echo 'Set PG_BIN to a PostgreSQL installation bin directory, or use Docker Compose.'; exit 1; fi
[ -f .env ] || { echo 'Run ./scripts/setup-local.sh first.'; exit 1; }
mkdir -p .local
# Parse .env as data; never execute it as shell code or print passwords.
"$TASK_ROOT/.venv/bin/python" - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
root=Path.cwd();data=dotenv_values(root/'.env')
p=root/'.local/pg-password';p.write_text(data['POSTGRES_PASSWORD']+'\n');p.chmod(0o600)
p=root/'.local/pgpass';p.write_text('127.0.0.1:55439:*:postgres:'+data['POSTGRES_PASSWORD']+'\n');p.chmod(0o600)
PY
export PGPASSFILE="$TASK_ROOT/.local/pgpass"
if [ ! -f .local/postgres/PG_VERSION ]; then
    "$TASK_PG_BIN/initdb" -D "$TASK_ROOT/.local/postgres" --username=postgres --pwfile="$TASK_ROOT/.local/pg-password" --auth=scram-sha-256 --encoding=UTF8 --locale=C > .local/initdb.log
fi
if ! "$TASK_PG_BIN/pg_ctl" -D "$TASK_ROOT/.local/postgres" status >/dev/null 2>&1; then
    "$TASK_PG_BIN/pg_ctl" -D "$TASK_ROOT/.local/postgres" -l "$TASK_ROOT/.local/postgres.log" -o "-h 127.0.0.1 -p 55439 -k $TASK_ROOT/.local" start
fi
for task_database in grounded_research grounded_research_test; do
    task_exists="$("$TASK_PG_BIN/psql" -h 127.0.0.1 -p 55439 -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$task_database'")"
    if [ "$task_exists" != '1' ]; then "$TASK_PG_BIN/createdb" -h 127.0.0.1 -p 55439 -U postgres "$task_database"; fi
done
printf '%s\n' 'Project PostgreSQL ready on 127.0.0.1:55439.'
