"""Tests for structural configuration defaults and secret handling."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
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
    assert settings.pdf_source_dir == Path("data/course-pdfs")
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


def test_embedding_model_and_dimensions_are_locked_to_schema_contract() -> None:
    import pytest

    with pytest.raises(ValidationError, match="embedding_model"):
        IsolatedSettings(embedding_model="other-model")
    with pytest.raises(ValidationError, match="embedding_dimensions"):
        IsolatedSettings(embedding_dimensions=384)


def test_runtime_retrieval_settings_are_semantically_validated() -> None:
    import pytest

    with pytest.raises(ValidationError, match="retrieval_limit"):
        IsolatedSettings(retrieval_limit=0)
    with pytest.raises(ValidationError, match="evidence_minimum_score"):
        IsolatedSettings(evidence_minimum_score=1.1)


def test_gemini_mode_requires_a_server_key_and_provider_mode_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        IsolatedSettings(rag_provider="gemini")
    assert IsolatedSettings(rag_provider="deterministic").rag_provider == "deterministic"


def test_minimum_score_defaults_by_provider_unless_configured() -> None:
    assert IsolatedSettings().minimum_score == 0.1
    assert IsolatedSettings(
        rag_provider="gemini", gemini_api_key=SecretStr("key")
    ).minimum_score == 0.6
    assert IsolatedSettings(evidence_minimum_score=0.5).minimum_score == 0.5


def test_empty_minimum_score_environment_value_uses_provider_default(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCE_MINIMUM_SCORE", "")
    assert IsolatedSettings().evidence_minimum_score is None


def test_fallback_answer_models_are_a_comma_separated_list() -> None:
    assert IsolatedSettings(answer_fallback_models=" a, b ,,c ").fallback_answer_models == (
        "a",
        "b",
        "c",
    )
    assert IsolatedSettings(answer_fallback_models="").fallback_answer_models == ()
