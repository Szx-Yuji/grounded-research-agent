"""Assistant integration fixtures isolated from the legacy destructive fixtures.

Run with --confcutdir=tests/assistant_integration. Only a dedicated local test database
is accepted, and only users created by these fixtures are removed.
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
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
test_connection = urlsplit(os.environ["DATABASE_URL"])
if (
    test_connection.hostname not in {"localhost", "127.0.0.1"}
    or test_connection.port != 55439
    or test_connection.path != "/grounded_research_test"
):
    raise RuntimeError("Integration tests require the dedicated local test database")

# Settings/engine must be imported only after choosing the isolated database.
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app import assistant as assistant_module  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import KnowledgeBase, User  # noqa: E402
from app.services import assistant_conversations  # noqa: E402


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID
    headers: dict[str, str]
    knowledge_base_id: uuid.UUID


@pytest.fixture(autouse=True)
def fake_assistant_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing explicit fake must fail instead of calling a paid provider."""

    def unexpected_execution(*_args, **_kwargs):
        raise AssertionError("This test did not configure a fake assistant engine")

    monkeypatch.setattr(assistant_module, "execute_chat", unexpected_execution)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "integration-placeholder")
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "integration-placeholder")
    monkeypatch.setattr(settings, "ZHIPUAI_API_KEY", "integration-placeholder")
    monkeypatch.setattr(settings, "RESEARCH_STREAM_POLL_SECONDS", 0.01)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(fastapi_app) as value:
        yield value
        with assistant_conversations._lock:
            for signal in assistant_conversations._active.values():
                signal.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with assistant_conversations._lock:
                if not assistant_conversations._active:
                    break
            time.sleep(0.01)
        with assistant_conversations._lock:
            assert not assistant_conversations._active, (
                "Test left a assistant worker active"
            )


@pytest.fixture
def actors(client: TestClient) -> Iterator[tuple[Actor, Actor]]:
    created: list[uuid.UUID] = []
    result = []
    for _ in range(2):
        email = f"assistant-integration-{uuid.uuid4().hex}@example.com"
        password = "Integration-fixture-only-2026"
        with Session(engine) as session:
            user = User(email=email, hashed_password=get_password_hash(password))
            session.add(user)
            session.commit()
            session.refresh(user)
            knowledge_base = KnowledgeBase(
                name="Integration knowledge", owner_id=user.id
            )
            session.add(knowledge_base)
            session.commit()
            session.refresh(knowledge_base)
            created.append(user.id)
            user_id, kb_id = user.id, knowledge_base.id
        response = client.post(
            "/api/v1/login/access-token",
            data={"username": email, "password": password},
        )
        assert response.status_code == 200
        result.append(
            Actor(
                user_id,
                {"Authorization": f"Bearer {response.json()['access_token']}"},
                kb_id,
            )
        )
    try:
        yield result[0], result[1]
    finally:
        # A user's deletion cascades only to this fixture's KBs/runs/events.
        # Worker tests explicitly release and await their workers before here.
        with Session(engine) as session:
            for user_id in created:
                user = session.get(User, user_id)
                if user is not None:
                    session.delete(user)
            session.commit()
