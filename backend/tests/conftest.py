# ruff: noqa: E402
# Validate the dedicated test database before importing application fixtures.
import os
from collections.abc import Generator
from urllib.parse import urlsplit

_test_url = urlsplit(os.environ.get("DATABASE_URL", ""))
if (
    _test_url.hostname not in {"localhost", "127.0.0.1"}
    or _test_url.path != "/grounded_research_test"
):
    raise RuntimeError(
        "Legacy tests may clear data: use the dedicated grounded_research_test database via scripts/check.sh"
    )

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import Item, User
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        init_db(session)
        yield session
        statement = delete(Item)
        session.execute(statement)
        statement = delete(User)
        session.execute(statement)
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )


@pytest.fixture(autouse=True)
def do_not_deliver_real_email(monkeypatch):
    """Legacy account tests exercise routes without sending external messages."""
    from app.api.routes import login, users, utils

    for module in (login, users, utils):
        monkeypatch.setattr(module, "send_email", lambda **_kwargs: None)
