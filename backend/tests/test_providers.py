"""Tests for direct provider adapters and deterministic offline doubles."""

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.gemini import GeminiEmbeddingProvider, GeminiGenerationProvider
from app.providers.groq import GroqGenerationProvider
from app.providers.openai import OpenAIGenerationProvider
from app.providers.protocols import ProviderConfigurationError


def test_deterministic_providers_are_repeatable_without_credentials() -> None:
    async def verify() -> None:
        embedder = DeterministicEmbeddingProvider()
        generator = DeterministicGenerationProvider("controlled answer")

        vector_a = await embedder.embed("same text")
        vector_b = await embedder.embed("same text")
        answer_a = await generator.generate("same prompt")
        answer_b = await generator.generate("same prompt")

        assert vector_a == vector_b
        assert vector_a != await embedder.embed("different text")
        assert len(vector_a) == 768
        assert answer_a == answer_b == "controlled answer"

        grounded = await DeterministicGenerationProvider().generate(
            'Question:\nA question\n\nEvidence:\n'
            '[{"citation_id":"chunk-1","excerpt":"A fact from the course."}]'
        )
        assert grounded == (
            '{"claims": [{"text": "A fact from the course.", '
            '"citation_ids": ["chunk-1"]}]}'
        )

    asyncio.run(verify())


def test_live_gemini_provider_requires_credentials_only_when_invoked() -> None:
    async def verify() -> None:
        embedder = GeminiEmbeddingProvider(None)
        generator = GeminiGenerationProvider(None)

        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            await embedder.embed("text")
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            await generator.generate("prompt")

    asyncio.run(verify())


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def embed_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append(("embed", model, config))
        item = type("Item", (), {"values": [0.1] * 768})()
        return type("EmbeddingResponse", (), {"embeddings": [item]})()

    async def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append(("generate", model, (contents, config)))
        return type("GenerationResponse", (), {"text": "direct response"})()


class FakeAsyncClient:
    def __init__(self) -> None:
        self.models = FakeModels()


class FakeClient:
    def __init__(self) -> None:
        self.aio = FakeAsyncClient()


def test_gemini_adapters_use_direct_async_sdk_methods() -> None:
    async def verify() -> None:
        client = FakeClient()
        embedding = GeminiEmbeddingProvider(
            SecretStr("test-only"), client=client  # type: ignore[arg-type]
        )
        generation = GeminiGenerationProvider(
            SecretStr("test-only"), client=client  # type: ignore[arg-type]
        )

        vector = await embedding.embed("course text")
        answer = await generation.generate("grounded prompt")

        assert len(vector) == 768
        assert answer == "direct response"
        assert client.aio.models.calls[0][0:2] == ("embed", "gemini-embedding-2")
        assert client.aio.models.calls[1][0:2] == ("generate", "gemini-3.8-flash")
        assert client.aio.models.calls[1][2][1].response_mime_type == "application/json"

    asyncio.run(verify())


def test_generation_rejects_empty_text_response() -> None:
    class EmptyModels(FakeModels):
        async def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
            return type("GenerationResponse", (), {"text": "  "})()

    class EmptyClient(FakeClient):
        def __init__(self) -> None:
            self.aio = type("AsyncClient", (), {"models": EmptyModels()})()

    async def verify() -> None:
        provider = GeminiGenerationProvider(
            SecretStr("test-only"), client=EmptyClient()  # type: ignore[arg-type]
        )
        with pytest.raises(RuntimeError, match="no text response"):
            await provider.generate("prompt")

    asyncio.run(verify())


def test_openai_generation_uses_json_mode_and_configured_model() -> None:
    class FakeResponses:
        def __init__(self) -> None:
            self.call: dict[str, Any] | None = None

        async def create(self, **kwargs: Any) -> Any:
            self.call = kwargs
            return type("Response", (), {"output_text": '{"claims": []}'})()

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    async def verify() -> None:
        client = FakeClient()
        provider = OpenAIGenerationProvider(
            SecretStr("test-only"), model="test-model", client=client  # type: ignore[arg-type]
        )
        result = await provider.generate("grounded prompt")

        assert result == '{"claims": []}'
        assert client.responses.call == {
            "model": "test-model",
            "input": "grounded prompt",
            "text": {"format": {"type": "json_object"}},
        }

    asyncio.run(verify())


def test_openai_generation_requires_key_and_rejects_empty_response() -> None:
    class EmptyResponses:
        async def create(self, **_kwargs: Any) -> Any:
            return type("Response", (), {"output_text": "  "})()

    class EmptyClient:
        responses = EmptyResponses()

    async def verify() -> None:
        with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
            await OpenAIGenerationProvider(None).generate("prompt")
        provider = OpenAIGenerationProvider(
            SecretStr("test-only"), client=EmptyClient()  # type: ignore[arg-type]
        )
        with pytest.raises(RuntimeError, match="no text response"):
            await provider.generate("prompt")

    asyncio.run(verify())


def test_groq_generation_uses_chat_json_mode_and_configured_model() -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.call: dict[str, Any] | None = None

        async def create(self, **kwargs: Any) -> Any:
            self.call = kwargs
            message = type("Message", (), {"content": '{"claims": []}'})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeClient:
        def __init__(self) -> None:
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    async def verify() -> None:
        client = FakeClient()
        provider = GroqGenerationProvider(
            SecretStr("test-only"), model="test-model", client=client  # type: ignore[arg-type]
        )
        result = await provider.generate("grounded prompt")

        assert result == '{"claims": []}'
        assert client.chat.completions.call == {
            "model": "test-model",
            "messages": [{"role": "user", "content": "grounded prompt"}],
            "response_format": {"type": "json_object"},
        }

    asyncio.run(verify())


def test_groq_generation_requires_key_and_rejects_empty_response() -> None:
    class FakeCompletions:
        async def create(self, **_kwargs: Any) -> Any:
            message = type("Message", (), {"content": None})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    async def verify() -> None:
        with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
            await GroqGenerationProvider(None).generate("prompt")
        provider = GroqGenerationProvider(
            SecretStr("test-only"), client=FakeClient()  # type: ignore[arg-type]
        )
        with pytest.raises(RuntimeError, match="no text response"):
            await provider.generate("prompt")

    asyncio.run(verify())
