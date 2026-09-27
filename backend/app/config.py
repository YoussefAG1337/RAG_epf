"""Structural runtime configuration for the API scaffold."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
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
    openai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    rag_provider: str = "deterministic"
    answer_provider: str | None = None
    answer_model: str = "gemini-3.8-flash"
    openai_answer_model: str = "gpt-4.1-mini"
    groq_answer_model: str = "openai/gpt-oss-20b"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 768
    retrieval_limit: int = 8
    evidence_minimum_score: float = 0.35
    frontend_origin: str = "http://localhost:3000"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @field_validator("answer_provider", mode="before")
    @classmethod
    def normalize_empty_answer_provider(cls, value: object) -> object:
        """Treat an empty environment setting as unset for Compose compatibility."""

        return None if value == "" else value

    @model_validator(mode="after")
    def validate_runtime_values(self) -> "Settings":
        """Keep runtime settings compatible with the fixed vector schema contract."""

        if self.embedding_model != "gemini-embedding-2":
            raise ValueError("embedding_model must be gemini-embedding-2 for the migrated schema")
        if self.embedding_dimensions != 768:
            raise ValueError("embedding_dimensions must be 768 for the migrated schema")
        if self.rag_provider not in {"deterministic", "gemini"}:
            raise ValueError("rag_provider must be deterministic or gemini")
        if self.answer_provider is not None and self.answer_provider not in {
            "deterministic",
            "gemini",
            "openai",
            "groq",
        }:
            raise ValueError("answer_provider must be deterministic, gemini, openai, or groq")
        if (self.rag_provider == "gemini" or self.selected_answer_provider == "gemini") and (
            self.gemini_api_key is None or not self.gemini_api_key.get_secret_value()
        ):
            raise ValueError("GEMINI_API_KEY is required when Gemini is selected")
        if self.selected_answer_provider == "openai" and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value()
        ):
            raise ValueError("OPENAI_API_KEY is required when ANSWER_PROVIDER=openai")
        if self.selected_answer_provider == "groq" and (
            self.groq_api_key is None or not self.groq_api_key.get_secret_value()
        ):
            raise ValueError("GROQ_API_KEY is required when ANSWER_PROVIDER=groq")
        if self.retrieval_limit < 1:
            raise ValueError("retrieval_limit must be greater than zero")
        if not 0.0 <= self.evidence_minimum_score <= 1.0:
            raise ValueError("evidence_minimum_score must be between zero and one")
        return self

    @property
    def selected_answer_provider(self) -> str:
        """Resolve answer generation independently while preserving legacy defaults."""

        return self.answer_provider or self.rag_provider

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
