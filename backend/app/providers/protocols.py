"""Narrow asynchronous provider contracts."""

from collections.abc import AsyncIterator
from typing import Protocol


class ProviderConfigurationError(RuntimeError):
    """Raised when a live provider is invoked without its required credentials."""


class ProviderUnavailableError(RuntimeError):
    """Every configured model is overloaded or rate limited; retrying later may succeed."""


class EmbeddingProvider(Protocol):
    """Embed a question (``embed``). Providers may also offer ``embed_batch`` for passages;
    ingestion uses it when present, since retrieval models encode passages differently."""

    async def embed(self, text: str) -> list[float]: ...


class GenerationProvider(Protocol):
    """Generate one response from a complete prompt (JSON unless ``json_output=False``)."""

    async def generate(self, prompt: str, *, json_output: bool = True) -> str: ...


class StreamingGenerationProvider(GenerationProvider, Protocol):
    """A generation provider that can also yield its JSON response incrementally."""

    def generate_stream(self, prompt: str) -> AsyncIterator[str]: ...
