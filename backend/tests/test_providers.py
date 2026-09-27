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
        assert vector_a != await embedder.embed("unrelated words")
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


def test_deterministic_embeddings_reflect_shared_vocabulary() -> None:
    async def verify() -> None:
        embedder = DeterministicEmbeddingProvider()

        def cosine(left: list[float], right: list[float]) -> float:
            return sum(a * b for a, b in zip(left, right, strict=True))

        chunk = await embedder.embed(
            "Une liste chaînée est composée de nœuds reliés par des pointeurs."
        )
        related = await embedder.embed("Qu'est-ce qu'une LISTE CHAINEE ?")
        unrelated = await embedder.embed("Théorème de Pythagore et triangle rectangle")

        assert abs(cosine(chunk, chunk) - 1.0) < 1e-9
        assert cosine(chunk, related) > 0.3
        assert cosine(chunk, unrelated) == 0.0
        assert await embedder.embed_batch(["liste chaînée"]) == [
            await embedder.embed("liste chaînée")
        ]
        # Stopword-only text still yields a usable unit vector.
        stopwords_only = await embedder.embed("de la")
        assert abs(cosine(stopwords_only, stopwords_only) - 1.0) < 1e-9

    asyncio.run(verify())


def test_gemini_batch_sends_each_text_as_its_own_content() -> None:
    class BatchModels:
        def __init__(self) -> None:
            self.contents: Any = None

        async def embed_content(self, *, model: str, contents: Any, config: Any) -> Any:
            self.contents = contents
            item = type("Item", (), {"values": [0.2] * 768})()
            return type("EmbeddingResponse", (), {"embeddings": [item] * len(contents)})()

    models = BatchModels()
    client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()
    provider = GeminiEmbeddingProvider(SecretStr("test-only"), client=client)  # type: ignore[arg-type]

    vectors = asyncio.run(provider.embed_batch(["premier", "second"]))

    assert len(vectors) == 2
    # A plain list of strings would be merged into one input by multimodal embedding models.
    assert [content.parts[0].text for content in models.contents] == ["premier", "second"]


def test_generation_falls_back_when_the_primary_model_is_overloaded() -> None:
    from google.genai import errors

    class OverloadedModels:
        def __init__(self, failing_code: int) -> None:
            self.failing_code = failing_code
            self.models: list[str] = []

        async def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
            self.models.append(model)
            if model == "primary":
                raise errors.APIError(self.failing_code, {"error": {"message": "busy"}})
            return type("GenerationResponse", (), {"text": '{"claims": []}'})()

    def provider(models: OverloadedModels) -> GeminiGenerationProvider:
        client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()
        return GeminiGenerationProvider(
            SecretStr("test-only"),
            model="primary",
            fallback_models=("fallback",),
            client=client,  # type: ignore[arg-type]
        )

    overloaded = OverloadedModels(503)
    assert asyncio.run(provider(overloaded).generate("prompt")) == '{"claims": []}'
    assert overloaded.models == ["primary", "fallback"]

    # A request error (bad prompt, bad key) is not retried on another model.
    rejected = OverloadedModels(400)
    with pytest.raises(errors.APIError):
        asyncio.run(provider(rejected).generate("prompt"))
    assert rejected.models == ["primary"]


def test_ingestion_embeddings_wait_out_the_per_minute_quota(monkeypatch) -> None:
    from google.genai import errors

    from app.providers import gemini

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(gemini.asyncio, "sleep", fake_sleep)
    quota = {"error": {"code": 429, "details": [{"retryDelay": "33s"}]}}

    class LimitedModels:
        def __init__(self, failures: int) -> None:
            self.failures = failures

        async def embed_content(self, *, model: str, contents: Any, config: Any) -> Any:
            if self.failures:
                self.failures -= 1
                raise errors.APIError(429, quota)
            item = type("Item", (), {"values": [0.3] * 768})()
            return type("EmbeddingResponse", (), {"embeddings": [item] * len(contents)})()

    def provider(failures: int, retries: int) -> GeminiEmbeddingProvider:
        aio = type("Aio", (), {"models": LimitedModels(failures)})()
        client = type("Client", (), {"aio": aio})()
        return GeminiEmbeddingProvider(
            SecretStr("test-only"), rate_limit_retries=retries, client=client  # type: ignore[arg-type]
        )

    assert len(asyncio.run(provider(failures=2, retries=5).embed_batch(["a", "b"]))) == 2
    assert waits == [34.0, 34.0]

    # Answering a question does not retry: it fails fast instead of hanging.
    with pytest.raises(errors.APIError):
        asyncio.run(provider(failures=1, retries=0).embed_batch(["a"]))
