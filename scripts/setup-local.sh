#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/'; exit 1; }
command -v bun >/dev/null || { echo 'Install Bun first: https://bun.sh/'; exit 1; }
python3 - <<'PY'
from pathlib import Path
import secrets
root=Path.cwd()
(root/'.local').mkdir(exist_ok=True)
path=root/'.env'
if not path.exists():
    password=secrets.token_hex(16)
    path.write_text('\n'.join([
        'FASTAPI_ENV=development',
        'PROJECT_NAME=Grounded Research',
        'DOMAIN=localhost',
        'FRONTEND_HOST=http://localhost:5178',
        'SECRET_KEY='+secrets.token_hex(32),
        'FIRST_SUPERUSER=admin@example.com',
        'FIRST_SUPERUSER_PASSWORD='+secrets.token_hex(12),
        'POSTGRES_PASSWORD='+password,
        f'DATABASE_URL=postgresql://postgres:{password}@127.0.0.1:55439/grounded_research',
        'DOCUMENT_STORAGE_DIR='+str(root/'.local/documents'),
        'CHROMA_PERSIST_DIRECTORY='+str(root/'.local/chroma'),
        'DEEPSEEK_API_KEY=',
        'DEEPSEEK_BASE_URL=https://api.deepseek.com',
        'DEEPSEEK_MODEL=deepseek-v4-flash',
        'ZHIPUAI_API_KEY=',
        'ZHIPUAI_EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4/',
        'ZHIPUAI_EMBEDDING_MODEL=embedding-3',
        'TAVILY_API_KEY=',
        'REDIS_URL=',
        'SMTP_HOST=',
        'EMAILS_FROM_EMAIL=',
        'SENTRY_DSN=',
    ])+'\n')
    path.chmod(0o600)
    print('Created .env with fresh local credentials. Add API keys there for real AI operations.')
else:
    print('Keeping existing .env.')
PY
uv sync --frozen --package app
bun install --frozen-lockfile --ignore-scripts
printf '%s\n' 'Setup complete. Start: ./scripts/start-local.sh'
