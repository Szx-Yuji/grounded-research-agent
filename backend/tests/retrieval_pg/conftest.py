"""Only explicitly provisioned fixture users in the dedicated local test DB."""

import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)
connection = urlsplit(os.environ["DATABASE_URL"])
os.environ["DATABASE_URL"] = urlunsplit(
    connection._replace(path="/grounded_research_test")
)
if connection.hostname not in {"localhost", "127.0.0.1"} or connection.port != 55439:
    raise RuntimeError(
        "This suite only writes fixtures to local port55439/grounded_research_test"
    )

from sqlmodel import Session  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.models import KnowledgeBase, KnowledgeDocument, User  # noqa: E402


@pytest.fixture
def owner():
    with Session(engine, expire_on_commit=False) as session:
        user = User(
            email=f"retrieval-pg-{uuid.uuid4().hex}@example.com",
            hashed_password="fixture-only",
        )
        session.add(user)
        session.flush()
        base = KnowledgeBase(name="Temporary lexical PG fixture", owner_id=user.id)
        session.add(base)
        session.flush()
        document = KnowledgeDocument(
            knowledge_base_id=base.id,
            original_filename="specifications.txt",
            storage_key=f"fixture/{uuid.uuid4()}",
            status="ready",
        )
        session.add(document)
        session.commit()
        identity = user.id
        yield SimpleNamespace(user_id=user.id, kb_id=base.id, document_id=document.id)
    with Session(engine) as session:
        user = session.get(User, identity)
        if user is not None:
            session.delete(user)
            session.commit()
