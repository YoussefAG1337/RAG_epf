"""Tests for the local embeddings client, Gemini answers, and deterministic doubles."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from google.genai import errors
from pydantic import SecretStr

from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.gemini import GeminiGenerationProvider
from app.providers.groq import GroqGenerationProvider
from app.providers.local import EmbeddingServiceError, LocalEmbeddingProvider
from app.providers.protocols import ProviderConfigurationError, ProviderUnavailableError


def test_deterministic_embeddings_are_repeatable_and_reflect_shared_vocabulary() -> None:
    async def verify() -> None:
        embedder = DeterministicEmbeddingProvider(dimensions=1024)

        def cosine(left: list[float], right: list[float]) -> float:
            return sum(a * b for a, b in zip(left, right, strict=True))

        chunk = await embedder.embed("Une liste chaînée est composée de nœuds reliés.")
        assert chunk == await embedder.embed("Une liste chaînée est composée de nœuds reliés.")
        assert len(chunk) == 1024
        assert cosine(chunk, await embedder.embed("Qu'est-ce qu'une LISTE CHAINEE ?")) > 0.3
        assert cosine(chunk, await embedder.embed("Théorème de Pythagore")) == 0.0

    asyncio.run(verify())


def test_deterministic_generation_quotes_the_best_excerpt_and_keeps_questions() -> None:
    async def verify() -> None:
        generator = DeterministicGenerationProvider()
        prompt = (
            'Question :\nq\n\nExtraits :\n[{"id": "S1", "extrait": "Première ligne.\\nSuite."}]'
        )
        assert json.loads(await generator.generate(prompt)) == {
            "claims": [
                {
                    "text": "Première ligne.\nSuite.",
                    "sources": [{"id": "S1", "quote": "Première ligne."}],
                }
            ]
        }
        rewrite = "Conversation :\n...\n\nDernière question :\nEt pour AES ?"
        assert await generator.generate(rewrite, json_output=False) == "Et pour AES ?"

    asyncio.run(verify())


def _service(handler: Any) -> LocalEmbeddingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LocalEmbeddingProvider(
        "http://embeddings:8080/", model="BAAI/bge-m3", dimensions=4, client=client
    )


def test_local_provider_encodes_questions_and_passages_differently() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), **body})
        vectors = [[0.5, 0.5, 0.5, 0.5] for _ in body["inputs"]]
        return httpx.Response(
            200, json={"model": "BAAI/bge-m3", "dimensions": 4, "embeddings": vectors}
        )

    async def verify() -> None:
        provider = _service(handler)
        assert await provider.embed("Comment fonctionne RSA ?") == [0.5] * 4
        assert len(await provider.embed_batch(["passage 1", "passage 2"])) == 2
        await provider.aclose()

    asyncio.run(verify())
    assert [(item["url"], item["kind"]) for item in requests] == [
        ("http://embeddings:8080/embed", "query"),
        ("http://embeddings:8080/embed", "document"),
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(200, json={"model": "other/model", "embeddings": [[0.1] * 4]}),
            "runs 'other/model'",
        ),
        (
            httpx.Response(200, json={"model": "BAAI/bge-m3", "embeddings": [[0.1] * 3]}),
            "wrong shape",
        ),
        (httpx.Response(503, json={"detail": "loading"}), "HTTPStatusError"),
    ],
)
def test_local_provider_rejects_wrong_models_shapes_and_failures(
    response: httpx.Response, message: str
) -> None:
    provider = _service(lambda _request: response)
    with pytest.raises(EmbeddingServiceError, match=message):
        asyncio.run(provider.embed("question"))


def test_local_provider_reports_an_unreachable_service() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EmbeddingServiceError, match="ConnectError"):
        asyncio.run(_service(refuse).embed("question"))


class _Models:
    def __init__(
        self, failures: dict[str, int] | None = None, stream: list[str] | None = None
    ) -> None:
        self.failures = failures or {}
        self.stream = stream or ['{"claims":', " []}"]
        self.calls: list[tuple[str, str, Any]] = []

    async def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append(("generate", model, config))
        if model in self.failures:
            raise errors.APIError(self.failures[model], {"error": {"message": "busy"}})
        return type("Response", (), {"text": '{"claims": []}'})()

    async def generate_content_stream(
        self, *, model: str, contents: str, config: Any
    ) -> AsyncIterator[Any]:
        self.calls.append(("stream", model, config))
        if model in self.failures:
            raise errors.APIError(self.failures[model], {"error": {"message": "busy"}})
        pieces = self.stream

        async def iterate() -> AsyncIterator[Any]:
            for piece in pieces:
                yield type("Chunk", (), {"text": piece})()

        return iterate()


def _gemini(
    models: _Models, fallbacks: tuple[str, ...] = ("fallback",)
) -> GeminiGenerationProvider:
    client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()
    return GeminiGenerationProvider(
        SecretStr("test-only"),
        model="primary",
        fallback_models=fallbacks,
        client=client,  # type: ignore[arg-type]
    )


def test_gemini_requires_credentials_only_when_invoked() -> None:
    with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
        asyncio.run(GeminiGenerationProvider(None).generate("prompt"))


def test_gemini_asks_for_json_answers_and_plain_text_rewrites() -> None:
    models = _Models()
    provider = _gemini(models)
    asyncio.run(provider.generate("prompt"))
    asyncio.run(provider.generate("prompt", json_output=False))
    assert [call[2].response_mime_type for call in models.calls] == [
        "application/json",
        "text/plain",
    ]


def test_gemini_falls_back_when_the_primary_model_is_overloaded() -> None:
    overloaded = _Models(failures={"primary": 503})
    assert asyncio.run(_gemini(overloaded).generate("prompt")) == '{"claims": []}'
    assert [call[1] for call in overloaded.calls] == ["primary", "fallback"]

    rejected = _Models(failures={"primary": 400})  # a bad request is not retried elsewhere
    with pytest.raises(errors.APIError):
        asyncio.run(_gemini(rejected).generate("prompt"))
    assert [call[1] for call in rejected.calls] == ["primary"]


def test_gemini_reports_unavailable_when_every_model_is_busy() -> None:
    async def collect(provider: GeminiGenerationProvider) -> str:
        return "".join([piece async for piece in provider.generate_stream("prompt")])

    busy = _Models(failures={"primary": 503, "fallback": 429})
    with pytest.raises(ProviderUnavailableError, match="last: 429"):
        asyncio.run(_gemini(busy).generate("prompt"))
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(collect(_gemini(busy)))


def test_gemini_streams_and_falls_back_before_the_first_chunk() -> None:
    async def collect(provider: GeminiGenerationProvider) -> str:
        return "".join([piece async for piece in provider.generate_stream("prompt")])

    models = _Models(failures={"primary": 429}, stream=['{"claims"', ": []}"])
    assert asyncio.run(collect(_gemini(models))) == '{"claims": []}'
    assert [call[:2] for call in models.calls] == [("stream", "primary"), ("stream", "fallback")]


def test_gemini_rejects_empty_responses() -> None:
    class Empty(_Models):
        async def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
            return type("Response", (), {"text": "  "})()

    with pytest.raises(RuntimeError, match="no text response"):
        asyncio.run(_gemini(Empty()).generate("prompt"))


def test_gemini_moves_on_when_a_model_stops_responding() -> None:
    class Stalling(_Models):
        async def generate_content_stream(
            self, *, model: str, contents: str, config: Any
        ) -> AsyncIterator[Any]:
            self.calls.append(("stream", model, config))

            async def iterate() -> AsyncIterator[Any]:
                if model == "primary":
                    await asyncio.sleep(10)  # accepted, then never answers
                yield type("Chunk", (), {"text": '{"claims": []}'})()

            return iterate()

    async def collect(provider: GeminiGenerationProvider) -> str:
        return "".join([piece async for piece in provider.generate_stream("prompt")])

    models = Stalling()
    client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()
    provider = GeminiGenerationProvider(
        SecretStr("test-only"),
        model="primary",
        fallback_models=("fallback",),
        client=client,  # type: ignore[arg-type]
        stall_timeout=0.05,
    )
    assert asyncio.run(collect(provider)) == '{"claims": []}'
    assert [call[1] for call in models.calls] == ["primary", "fallback"]

    only = GeminiGenerationProvider(
        SecretStr("test-only"),
        model="primary",
        client=type("Client", (), {"aio": type("Aio", (), {"models": Stalling()})()})(),  # type: ignore[arg-type]
        stall_timeout=0.05,
    )
    with pytest.raises(ProviderUnavailableError, match="stopped responding"):
        asyncio.run(collect(only))


def _groq(
    handler: Any, fallbacks: tuple[str, ...] = ("fallback",), stall_timeout: float = 30.0
) -> GroqGenerationProvider:
    return GroqGenerationProvider(
        SecretStr("test-only"),
        model="primary",
        fallback_models=fallbacks,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        stall_timeout=stall_timeout,
    )


def _groq_sse(*pieces: str) -> bytes:
    events = [
        "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) for piece in pieces
    ]
    return ("\n\n".join([*events, "data: [DONE]"]) + "\n\n").encode()


async def _collect_groq(provider: GroqGenerationProvider) -> str:
    return "".join([piece async for piece in provider.generate_stream("prompt")])


def test_groq_requires_credentials_only_when_invoked() -> None:
    with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
        asyncio.run(GroqGenerationProvider(None).generate("prompt"))


def test_groq_asks_for_json_answers_and_plain_text_rewrites() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"claims": []}'}}]})

    provider = _groq(handler)
    assert asyncio.run(provider.generate("prompt")) == '{"claims": []}'
    asyncio.run(provider.generate("prompt", json_output=False))
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in bodies[1]
    assert bodies[0]["messages"] == [{"role": "user", "content": "prompt"}]


def test_groq_falls_back_on_overload_but_not_on_bad_requests() -> None:
    calls: list[str] = []

    def handler_for(status: int) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            calls.append(model)
            if model == "primary":
                return httpx.Response(status, json={"error": {"message": "no"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

        return handler

    assert asyncio.run(_groq(handler_for(429)).generate("prompt")) == "{}"
    assert calls == ["primary", "fallback"]

    calls.clear()
    with pytest.raises(RuntimeError, match="400"):
        asyncio.run(_groq(handler_for(400)).generate("prompt"))
    assert calls == ["primary"]


def test_groq_reports_unavailable_when_every_model_is_busy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "over capacity"}})

    with pytest.raises(ProviderUnavailableError, match="last: 503"):
        asyncio.run(_groq(handler).generate("prompt"))
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_collect_groq(_groq(handler)))


def test_groq_streams_and_falls_back_before_the_first_chunk() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        assert body["stream"] is True
        if body["model"] == "primary":
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, content=_groq_sse('{"claims"', ": []}"))

    assert asyncio.run(_collect_groq(_groq(handler))) == '{"claims": []}'
    assert calls == ["primary", "fallback"]


def test_groq_moves_on_when_a_model_stops_responding() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model"] == "primary":
            await asyncio.sleep(10)  # accepted, then never answers
        return httpx.Response(200, content=_groq_sse('{"claims": []}'))

    assert asyncio.run(_collect_groq(_groq(handler, stall_timeout=0.05))) == '{"claims": []}'
    with pytest.raises(ProviderUnavailableError, match="stopped responding"):
        asyncio.run(_collect_groq(_groq(handler, fallbacks=(), stall_timeout=0.05)))
