"""Structural runtime configuration for the API scaffold."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Environment-backed application settings with safe local defaults."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_parse_none_str=""
    )

    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "course_rag"
    database_user: str = "course_rag"
    database_password: SecretStr = SecretStr("local-development-only")
    pdf_source_dir: Path = Path("data/course-pdfs")
    gemini_api_key: SecretStr | None = None
    rag_provider: str = "deterministic"
    answer_model: str = "gemini-3.8-flash"
    # Comma-separated models tried in order when the answer model is overloaded (503) or
    # rate limited (429); empty disables. Free-tier demand spikes can hit several at once.
    answer_fallback_models: str = "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash-lite"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 768
    retrieval_limit: int = 8
    # Unset means "use the provider default" (see minimum_score).
    evidence_minimum_score: float | None = None
    # Comma-separated browser origins allowed to call the API (CORS). Browsers treat
    # localhost and 127.0.0.1 as different origins, so both are allowed by default.
    frontend_origin: str = "http://localhost:3000,http://127.0.0.1:3000"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @model_validator(mode="after")
    def validate_runtime_values(self) -> "Settings":
        """Keep runtime settings compatible with the fixed vector schema contract."""

        if self.embedding_model != "gemini-embedding-2":
            raise ValueError("embedding_model must be gemini-embedding-2 for the migrated schema")
        if self.embedding_dimensions != 768:
            raise ValueError("embedding_dimensions must be 768 for the migrated schema")
        if self.rag_provider not in {"deterministic", "gemini"}:
            raise ValueError("rag_provider must be deterministic or gemini")
        if self.rag_provider == "gemini" and (
            self.gemini_api_key is None or not self.gemini_api_key.get_secret_value()
        ):
            raise ValueError("GEMINI_API_KEY is required when RAG_PROVIDER=gemini")
        if self.retrieval_limit < 1:
            raise ValueError("retrieval_limit must be greater than zero")
        if self.evidence_minimum_score is not None and not (
            0.0 <= self.evidence_minimum_score <= 1.0
        ):
            raise ValueError("evidence_minimum_score must be between zero and one")
        return self

    @property
    def frontend_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origin.split(",") if origin.strip()]

    @property
    def fallback_answer_models(self) -> tuple[str, ...]:
        return tuple(
            name.strip() for name in self.answer_fallback_models.split(",") if name.strip()
        )

    @property
    def minimum_score(self) -> float:
        """Evidence cutoff: explicit setting, else a default suited to the provider.

        gemini-embedding-2 scores unrelated French course text around 0.50-0.55 and relevant
        chunks around 0.70-0.80, so 0.6 separates them; lexical offline vectors score 0 for
        unrelated text and 0.2-0.4 for relevant text.
        """

        if self.evidence_minimum_score is not None:
            return self.evidence_minimum_score
        return 0.6 if self.rag_provider == "gemini" else 0.1

    @property
    def database_url(self) -> str:
        """Build a driver URL from unescaped connection fields."""

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the current process."""

    return Settings()
