"""Provider-free SQLite projection tests; no app .env or user DB is read."""

import os

for key, value in {
    "SECRET_KEY": "retrieval-test-secret-no-real-credentials-00000000",
    "PROJECT_NAME": "retrieval unit tests",
    "DATABASE_URL": "postgresql://fixture:fixture@127.0.0.1:55439/grounded_research_test",
    "FIRST_SUPERUSER": "fixture@example.com",
    "FIRST_SUPERUSER_PASSWORD": "fixture-password-only",
}.items():
    os.environ.setdefault(key, value)
