import warnings
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    EmailStr,
    Field,
    HttpUrl,
    PostgresDsn,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None

    DOCUMENT_STORAGE_DIR: Path = Path("/app/storage/documents")
    MAX_DOCUMENT_SIZE_BYTES: int = 20 * 1024 * 1024

    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_BASE_URL: str = ""
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    TAVILY_API_KEY: str | None = None
    REDIS_URL: str | None = None
    RESEARCH_MAX_WORKERS: int = Field(default=4, ge=1, le=32)
    RESEARCH_TIMEOUT_SECONDS: int = Field(default=300, ge=5, le=3600)
    RESEARCH_STREAM_POLL_SECONDS: float = Field(default=0.3, ge=0.01, le=30)
    WORKER_POLL_SECONDS: float = Field(default=0.5, ge=0.01, le=30)
    WORKER_LEASE_SECONDS: int = Field(default=30, ge=2, le=600)
    WORKER_HEARTBEAT_SECONDS: float = Field(default=5.0, ge=0.1, le=120)
    WORKER_QUEUE_LIMIT: int = Field(default=100, ge=1, le=10000)
    WORKER_OWNER_QUEUE_LIMIT: int = Field(default=10, ge=1, le=1000)
    EXECUTION_MAX_MODEL_CALLS: int = 24
    EXECUTION_MAX_SEARCH_CALLS: int = 16
    EXECUTION_MAX_EMBEDDING_CALLS: int = 12
    EXECUTION_MAX_OUTPUT_TOKENS: int = 50000
    EXECUTION_MAX_TOOL_CALLS: int = 30

    ZHIPUAI_API_KEY: str | None = None
    ZHIPUAI_EMBEDDING_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    ZHIPUAI_EMBEDDING_MODEL: str = "embedding-3"

    CHROMA_PERSIST_DIRECTORY: Path = Path("/app/storage/chroma")

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        for name in (
            "RESEARCH_MAX_WORKERS",
            "RESEARCH_TIMEOUT_SECONDS",
            "RESEARCH_STREAM_POLL_SECONDS",
            "WORKER_POLL_SECONDS",
            "WORKER_LEASE_SECONDS",
            "WORKER_HEARTBEAT_SECONDS",
            "WORKER_QUEUE_LIMIT",
            "WORKER_OWNER_QUEUE_LIMIT",
            "EXECUTION_MAX_MODEL_CALLS",
            "EXECUTION_MAX_SEARCH_CALLS",
            "EXECUTION_MAX_EMBEDDING_CALLS",
            "EXECUTION_MAX_OUTPUT_TOKENS",
            "EXECUTION_MAX_TOOL_CALLS",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.WORKER_HEARTBEAT_SECONDS >= self.WORKER_LEASE_SECONDS / 2:
            raise ValueError(
                "WORKER_HEARTBEAT_SECONDS must be less than half the lease"
            )
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
