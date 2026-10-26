"""Providers: local embeddings, Gemini or Groq answers, and offline test doubles.

The factories below are the one place that turns settings into providers, so the API,
the ingestion command, and the evaluation always use the same configuration.
"""

from typing import Any, Literal

from app.config import Settings
from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.gemini import GeminiGenerationProvider
from app.providers.groq import GroqGenerationProvider
from app.providers.local import LocalEmbeddingProvider
from app.providers.protocols import (
    EmbeddingProvider,
    GenerationProvider,
    ProviderConfigurationError,
    ProviderUnavailableError,
)


def create_embedding_provider(
    settings: Settings, provider: Literal["local", "deterministic"] | None = None
) -> EmbeddingProvider:
    """The configured embedding provider (``provider`` overrides EMBEDDING_PROVIDER)."""

    if (provider or settings.embedding_provider) == "local":
        return LocalEmbeddingProvider(
            settings.embedding_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    return DeterministicEmbeddingProvider(settings.embedding_dimensions)


def create_generation_provider(settings: Settings) -> GenerationProvider:
    """The configured answer provider (ANSWER_PROVIDER)."""

    if settings.answer_provider == "gemini":
        return GeminiGenerationProvider(
            settings.gemini_api_key,
            model=settings.answer_model,
            fallback_models=settings.fallback_answer_models,
            temperature=settings.answer_temperature,
        )
    if settings.answer_provider == "groq":
        return GroqGenerationProvider(
            settings.groq_api_key,
            model=settings.answer_model,
            fallback_models=settings.fallback_answer_models,
            temperature=settings.answer_temperature,
        )
    return DeterministicGenerationProvider()


async def close_provider(provider: Any) -> None:
    """Release a provider's HTTP resources, whichever close method it offers."""

    aclose = getattr(provider, "aclose", None)
    if aclose is not None:
        await aclose()
        return
    close = getattr(provider, "close", None)
    if close is not None:
        close()


__all__ = [
    "DeterministicEmbeddingProvider",
    "DeterministicGenerationProvider",
    "EmbeddingProvider",
    "GeminiGenerationProvider",
    "GenerationProvider",
    "GroqGenerationProvider",
    "LocalEmbeddingProvider",
    "ProviderConfigurationError",
    "ProviderUnavailableError",
    "close_provider",
    "create_embedding_provider",
    "create_generation_provider",
]
