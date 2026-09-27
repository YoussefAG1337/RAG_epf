"""Thin asynchronous adapters for the official Google Gen AI Python SDK."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from google import genai
from google.genai import errors, types
from pydantic import SecretStr

from app.providers.protocols import ProviderConfigurationError

logger = logging.getLogger(__name__)
# Waits longer than this mean a daily quota is exhausted, not a per-minute window.
MAXIMUM_RATE_LIMIT_WAIT_SECONDS = 120.0


class RateLimitExceededError(RuntimeError):
    """Gemini kept rejecting requests for quota reasons."""


def _retry_delay_seconds(error: errors.APIError) -> float:
    """Read Google's suggested wait ("retryDelay": "33s") from a 429 response."""

    details = error.details if isinstance(error.details, dict) else {}
    for item in details.get("error", {}).get("details", []) or []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str) and (match := re.fullmatch(r"(\d+(?:\.\d+)?)s", delay)):
            return float(match.group(1))
    return 30.0


async def _with_rate_limit_retries[Result](
    call: Callable[[], Awaitable[Result]], retries: int
) -> Result:
    """Run ``call``, waiting out per-minute quota errors (429) up to ``retries`` times."""

    for attempt in range(retries + 1):
        try:
            return await call()
        except errors.APIError as error:
            if error.code != 429 or attempt == retries:
                raise
            delay = _retry_delay_seconds(error) + 1.0
            if delay > MAXIMUM_RATE_LIMIT_WAIT_SECONDS:
                raise RateLimitExceededError(
                    f"Gemini quota exhausted (suggested wait {delay:.0f}s); "
                    "try again later or use a paid plan"
                ) from error
            logger.warning("Gemini rate limit reached; waiting %.0fs before retrying", delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


class GeminiEmbeddingProvider:
    """Embed text through Gemini with an explicit output dimensionality."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "gemini-embedding-2",
        dimensions: int = 768,
        rate_limit_retries: int = 0,
        client: genai.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        # Batch ingestion waits out per-minute quotas; answering a question fails fast.
        self._rate_limit_retries = rate_limit_retries
        self._client = client

    @property
    def provider_id(self) -> str:
        return "gemini"

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding input must not be empty")
        client = self._get_client()
        response = await _with_rate_limit_retries(
            lambda: client.aio.models.embed_content(
                model=self._model,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=self._dimensions),
            ),
            self._rate_limit_retries,
        )
        if not response.embeddings:
            raise RuntimeError("Gemini returned no embedding")
        values = response.embeddings[0].values
        if values is None or len(values) != self._dimensions:
            actual = 0 if values is None else len(values)
            raise RuntimeError(
                "Gemini returned an embedding with "
                f"{actual} dimensions; expected {self._dimensions}"
            )
        return [float(value) for value in values]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed several texts in one request, preserving input order."""

        if any(not text.strip() for text in texts):
            raise ValueError("embedding input must not be empty")
        client = self._get_client()
        # Each text must be its own Content: multimodal models such as gemini-embedding-2
        # treat a plain list of strings as the parts of ONE input and return one vector.
        contents = [types.Content(parts=[types.Part(text=text)]) for text in texts]
        response = await _with_rate_limit_retries(
            lambda: client.aio.models.embed_content(
                model=self._model,
                contents=contents,
                config=types.EmbedContentConfig(output_dimensionality=self._dimensions),
            ),
            self._rate_limit_retries,
        )
        embeddings = response.embeddings or []
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Gemini returned {len(embeddings)} embeddings for {len(texts)} inputs"
            )
        vectors: list[list[float]] = []
        for embedding in embeddings:
            values = embedding.values
            if values is None or len(values) != self._dimensions:
                actual = 0 if values is None else len(values)
                raise RuntimeError(
                    "Gemini returned an embedding with "
                    f"{actual} dimensions; expected {self._dimensions}"
                )
            vectors.append([float(value) for value in values])
        return vectors

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        if self._api_key is None or not self._api_key.get_secret_value():
            raise ProviderConfigurationError(
                "GEMINI_API_KEY is required when the Gemini provider is invoked"
            )
        self._client = genai.Client(api_key=self._api_key.get_secret_value())
        return self._client

    def close(self) -> None:
        """Release the SDK client's underlying HTTP resources, if created."""
        if self._client is not None:
            self._client.close()
            self._client = None


# Overload and rate-limit responses that another model may still serve.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 503, 504})


class GeminiGenerationProvider:
    """Generate a complete response through the direct Gemini SDK boundary.

    When the primary model is overloaded or rate limited, the fallback models are tried in
    order, so a demand spike on one model does not fail every question.
    """

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "gemini-3.8-flash",
        fallback_models: tuple[str, ...] = (),
        client: genai.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._models = (model, *(item for item in fallback_models if item != model))
        self._client = client

    async def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        client = self._get_client()
        for position, model in enumerate(self._models):
            try:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
            except errors.APIError as error:
                is_last = position == len(self._models) - 1
                if is_last or error.code not in _RETRYABLE_STATUS_CODES:
                    raise
                continue
            if response.text is None or not response.text.strip():
                raise RuntimeError("Gemini returned no text response")
            return response.text
        raise RuntimeError("no Gemini generation model is configured")

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        if self._api_key is None or not self._api_key.get_secret_value():
            raise ProviderConfigurationError(
                "GEMINI_API_KEY is required when the Gemini provider is invoked"
            )
        self._client = genai.Client(api_key=self._api_key.get_secret_value())
        return self._client

    def close(self) -> None:
        """Release the SDK client's underlying HTTP resources, if created."""
        if self._client is not None:
            self._client.close()
            self._client = None
