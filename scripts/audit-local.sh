#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
mkdir -p .local
bun --no-env-file audit --json
uv export --frozen --all-groups --no-hashes --no-emit-workspace --no-annotate -o .local/python-audit-requirements.txt
# Keep the raw report, then apply only the inherited, reviewed embedded-Chroma policy.
uvx pip-audit --strict --no-deps --disable-pip -r .local/python-audit-requirements.txt -f json -o .local/python-audit.json --progress-spinner off || true
if [[ "$(date -u +%F)" > "2026-11-21" ]]; then
    echo 'The embedded-Chroma exception review date has passed; review SECURITY.md.'
    exit 1
fi
uvx pip-audit --strict --no-deps --disable-pip -r .local/python-audit-requirements.txt --progress-spinner off \
    --ignore-vuln GHSA-f4j7-r4q5-qw2c \
    --ignore-vuln GHSA-36p7-vc44-83pf \
    --ignore-vuln GHSA-2wm9-hf6c-p5cr \
    --ignore-vuln GHSA-xph7-9rjv-w5fr
