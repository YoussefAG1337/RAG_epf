"""Keep tests independent of the developer's local .env and environment."""

from collections.abc import Iterator

import pytest

from app.config import Settings, get_settings

_LOCAL_VARIABLES = (
    "GEMINI_API_KEY",
    "RAG_PROVIDER",
    "ANSWER_MODEL",
    "ANSWER_FALLBACK_MODELS",
    "EVIDENCE_MINIMUM_SCORE",
    "PDF_SOURCE_DIR",
    "DATABASE_PORT",
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in _LOCAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
