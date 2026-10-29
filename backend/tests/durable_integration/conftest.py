"""Real PG durability tests; local dedicated DB, no provider credentials/calls."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import pytest
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for key, value in dotenv_values(PROJECT_ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)
connection = urlsplit(os.environ["DATABASE_URL"])
os.environ["DATABASE_URL"] = urlunsplit(
    connection._replace(path="/grounded_research_test")
)
local = urlsplit(os.environ["DATABASE_URL"])
if (
    local.hostname not in {"localhost", "127.0.0.1"}
    or local.port != 55439
    or local.path != "/grounded_research_test"
):
    raise RuntimeError(
        "Durable tests require the dedicated local PostgreSQL test database"
    )

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app import assistant, research  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture(autouse=True)
def no_providers(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("A durability test must not call a real provider")

    monkeypatch.setattr(assistant, "execute_chat", forbidden)
    monkeypatch.setattr(research, "execute_research", forbidden)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setattr(settings, "ZHIPUAI_API_KEY", "fixture-not-a-real-key")


@pytest.fixture
def actor() -> Iterator[SimpleNamespace]:
    from datetime import timedelta

    with Session(engine) as session:
        user = User(
            email=f"durable-{uuid.uuid4().hex}@example.com",
            hashed_password="fixture-only-no-login",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        identity = user.id
    token = create_access_token(identity, timedelta(minutes=30))
    try:
        yield SimpleNamespace(id=identity, headers={"Authorization": f"Bearer {token}"})
    finally:
        with Session(engine) as session:
            user = session.get(User, identity)
            if user:
                session.delete(user)
                session.commit()


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
