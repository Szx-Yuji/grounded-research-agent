import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import text
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import settings
from app.core.db import engine
from app.services.assistant_conversations import (
    recover_interrupted_turns,
    shutdown_assistant,
)
from app.services.research_runs import recover_interrupted_runs, shutdown_research

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A DB-scoped advisory lock enforces the process-local worker contract.
    # A second server must not mark a live server's runs as interrupted.
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(748382951)"))
        connection.commit()
        if not acquired:
            raise RuntimeError(
                "Grounded Research requires one backend process per database. Stop the other instance first."
            )
        try:
            await asyncio.to_thread(recover_interrupted_runs)
            await asyncio.to_thread(recover_interrupted_turns)
            yield
        finally:
            shutdown_research()
            shutdown_assistant()
            connection.execute(text("SELECT pg_advisory_unlock(748382951)"))
            connection.commit()


app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.frontend("/", directory=FRONTEND_DIR)
