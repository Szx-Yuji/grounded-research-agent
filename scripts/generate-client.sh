#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT/backend"
"$TASK_ROOT/.venv/bin/python" -c 'import json; from app.main import app; print(json.dumps(app.openapi()))' > "$TASK_ROOT/frontend/openapi.json"
cd "$TASK_ROOT/frontend"
bun run generate-client
