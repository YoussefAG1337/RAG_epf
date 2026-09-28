"""Client for the local embeddings container (see embeddings/service.py)."""

from __future__ import annotations

from typing import Literal

import httpx

# First requests after the container starts may wait for the model to finish loading.
_TIMEOUT = httpx.Timeout(300.0, connect=5.0)


class EmbeddingServiceError(RuntimeError):
    """The embeddings container is unreachable or serves an unexpected model."""


class LocalEmbeddingProvider:
    """Embed questions and course passages with the locally served model.

    ``embed`` encodes a question and ``embed_batch`` encodes course passages: retrieval
    models encode the two differently, and the service applies the right convention.
    """

    def __init__(
        self,
        url: str,
        *,
        model: str,
        dimensions: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._client = client

    @property
    def provider_id(self) -> str:
        return "local"

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding input must not be empty")
        return (await self._request([text], "query"))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("embedding input must not be empty")
        return await self._request(texts, "document")

    async def _request(
        self, texts: list[str], kind: Literal["query", "document"]
    ) -> list[list[float]]:
        client = self._get_client()
        try:
            response = await client.post(f"{self._url}/embed", json={"inputs": texts, "kind": kind})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise EmbeddingServiceError(
                f"embeddings service at {self._url} failed: {type(error).__name__}"
            ) from error
        body = response.json()
        if body.get("model") != self._model:
            raise EmbeddingServiceError(
                f"embeddings service runs {body.get('model')!r}, expected {self._model!r}; "
                "set the same EMBEDDING_MODEL for the service and the API"
            )
        vectors = body.get("embeddings") or []
        if len(vectors) != len(texts) or any(len(v) != self._dimensions for v in vectors):
            raise EmbeddingServiceError("embeddings service returned vectors of the wrong shape")
        return [[float(value) for value in vector] for vector in vectors]

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
