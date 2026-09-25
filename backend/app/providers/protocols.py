"""Narrow asynchronous provider contracts used by later RAG stories."""

from typing import Protocol


class ProviderConfigurationError(RuntimeError):
    """Raised when a live provider is invoked without its required credentials."""


class EmbeddingProvider(Protocol):
    """Generate one dense embedding for input text."""

    async def embed(self, text: str) -> list[float]: ...


class GenerationProvider(Protocol):
    """Generate one text response from a complete prompt."""

    async def generate(self, prompt: str) -> str: ...
