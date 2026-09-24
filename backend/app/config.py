"""Structural runtime configuration for the API scaffold."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Environment-backed application settings with safe local defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_host: str = "localhost"
    database_name: str = "course_rag"
    database_user: str = "course_rag"
    database_password: SecretStr = SecretStr("local-development-only")
    pdf_source_dir: Path = Path("data/course-pdfs")
    gemini_api_key: SecretStr | None = None
    answer_model: str = "gemini-3.8-flash"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 768
    retrieval_limit: int = 8
    evidence_minimum_score: float = 0.35
    frontend_origin: str = "http://localhost:3000"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @model_validator(mode="after")
    def validate_runtime_values(self) -> "Settings":
        """Keep runtime settings compatible with the fixed vector schema contract."""

        if self.embedding_model != "gemini-embedding-2":
            raise ValueError("embedding_model must be gemini-embedding-2 for the migrated schema")
        if self.embedding_dimensions != 768:
            raise ValueError("embedding_dimensions must be 768 for the migrated schema")
        if self.retrieval_limit < 1:
            raise ValueError("retrieval_limit must be greater than zero")
        if not 0.0 <= self.evidence_minimum_score <= 1.0:
            raise ValueError("evidence_minimum_score must be between zero and one")
        return self

    @property
    def database_url(self) -> str:
        """Build a driver URL from unescaped connection fields."""

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=5432,
            database=self.database_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the current process."""

    return Settings()
