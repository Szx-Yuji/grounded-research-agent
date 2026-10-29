#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
./scripts/start-db.sh
.venv/bin/python - <<'PY'
import os,subprocess
from pathlib import Path
from dotenv import dotenv_values
root=Path.cwd();env=os.environ.copy()
env.update({k:v for k,v in dotenv_values(root/'.env').items() if v is not None})
# All database-writing checks use a separate database; never clear local user data.
env['DATABASE_URL']=env['DATABASE_URL'].rsplit('/',1)[0]+'/grounded_research_test'
subprocess.run([str(root/'.venv/bin/alembic'),'upgrade','head'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/pytest'),'--confcutdir=tests/research','tests/research','-q'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/pytest'),'--confcutdir=tests/integration','tests/integration','-q'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/pytest'),'--confcutdir=tests/assistant','tests/assistant','-q'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/pytest'),'--confcutdir=tests/assistant_integration','tests/assistant_integration','-q'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/pytest'),'tests/api','tests/crud','-q'],cwd=root/'backend',env=env,check=True)
PY
.venv/bin/ruff check backend/app
(cd frontend && bun run build)
printf '%s\n' 'Backend checks and production frontend build passed.'
