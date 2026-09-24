"""Tests for structural configuration defaults and secret handling."""

from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from pytest import MonkeyPatch
from sqlalchemy.engine import make_url

from app.config import Settings


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


def test_settings_have_offline_defaults(monkeypatch: MonkeyPatch) -> None:
    # The developer's environment may contain a provider key; defaults must remain testable offline.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = IsolatedSettings()

    assert settings.answer_model == "gemini-3.8-flash"
    assert settings.embedding_model == "gemini-embedding-2"
    assert settings.embedding_dimensions == 768
    assert settings.gemini_api_key is None


def test_settings_do_not_serialize_provider_secret() -> None:
    settings = IsolatedSettings()
    settings.gemini_api_key = SecretStr("test-secret")

    assert "test-secret" not in settings.model_dump_json()


def test_database_url_encodes_reserved_password_characters() -> None:
    settings = IsolatedSettings(
        database_host="database",
        database_name="course_rag",
        database_user="course_rag",
        database_password=SecretStr("pa@ss:/?#%"),
    )

    assert make_url(settings.database_url).password == "pa@ss:/?#%"
    assert "pa%40ss%3A%2F%3F%23%25" in settings.database_url
