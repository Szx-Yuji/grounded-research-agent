from fastapi import APIRouter, Depends, HTTPException
from pydantic.networks import EmailStr
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.core.db import engine
from app.models import ExecutionJob, Message
from app.runtime.metrics import registry
from app.runtime.models import RuntimeCall
from app.utils import generate_test_email, send_email

router = APIRouter(prefix="/utils", tags=["utils"])


@router.post(
    "/test-email/",
    dependencies=[Depends(get_current_active_superuser)],
    status_code=201,
)
def test_email(email_to: EmailStr) -> Message:
    """
    Test emails.
    """
    email_data = generate_test_email(email_to=email_to)
    send_email(
        email_to=email_to,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Test email sent")


@router.get("/health-check/")
async def health_check() -> bool:
    return True


@router.get("/ready/")
def ready() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ready"}


@router.get("/metrics")
def metrics(session: SessionDep, current_user: CurrentUser) -> dict:
    """Low-cardinality operator metrics; restricted to superusers."""
    if not current_user.is_superuser:
        raise HTTPException(403, "Metrics require administrator access")
    jobs = session.exec(
        select(ExecutionJob.status, func.count()).group_by(ExecutionJob.status)
    ).all()
    calls = session.exec(
        select(RuntimeCall.status, func.count()).group_by(RuntimeCall.status)
    ).all()
    return {
        "process": registry.snapshot(),
        "database": {
            "jobs_by_status": dict(jobs),
            "calls_by_status": dict(calls),
        },
    }
