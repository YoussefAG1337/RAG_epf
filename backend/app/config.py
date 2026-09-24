"""Structural runtime configuration for the API scaffold."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Environment-backed application settings with safe local defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_host: str = "localhost"
    database_name: str = "course_rag"
    database_user: str = "course_rag"
    database_password: SecretStr = SecretStr("local-development-only")
    gemini_api_key: SecretStr | None = None
    answer_model: str = "gemini-3.8-flash"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 768
    retrieval_limit: int = 8
    evidence_minimum_score: float = 0.35
    frontend_origin: str = "http://localhost:3000"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

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
