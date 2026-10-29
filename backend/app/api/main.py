from fastapi import APIRouter

from app.api.routes import (
    assistant,
    conversations,
    items,
    knowledge_bases,
    login,
    private,
    research,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(conversations.router)
api_router.include_router(research.router)
api_router.include_router(assistant.router)

if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
