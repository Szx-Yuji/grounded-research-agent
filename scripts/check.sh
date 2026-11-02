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
for suite in ('runtime', 'mcp_skills', 'retrieval', 'research', 'assistant', 'integration', 'assistant_integration', 'durable_integration', 'runtime_integration', 'retrieval_pg'):
    subprocess.run([str(root/'.venv/bin/pytest'),f'--confcutdir=tests/{suite}',f'tests/{suite}','-q'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/pytest'),'tests/api','tests/crud','-q'],cwd=root/'backend',env=env,check=True)
subprocess.run([str(root/'.venv/bin/python'),'evals/retrieval/run.py','--mode','fixture'],cwd=root,env=env,check=True)
subprocess.run([str(root/'.venv/bin/python'),'evals/agent/run.py'],cwd=root,env=env,check=True)

PY
.venv/bin/ruff check backend/app backend/tests/runtime backend/tests/runtime_integration backend/tests/mcp_skills backend/tests/retrieval backend/tests/retrieval_pg backend/tests/durable_integration
(cd frontend && bun run build)
printf '%s\n' 'Backend checks and production frontend build passed.'
