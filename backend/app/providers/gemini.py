"""Answer generation through the official Google Gen AI Python SDK.

Embeddings are computed locally (see providers/local.py); Gemini only writes answers.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from google import genai
from google.genai import errors, types
from pydantic import SecretStr

from app.providers.protocols import ProviderConfigurationError, ProviderUnavailableError

logger = logging.getLogger(__name__)

# Overload and rate-limit responses that another model may still serve.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 503, 504})
# Seconds without any response (or between streamed chunks) before a model is considered
# stalled. The free tier sometimes accepts a request and then never answers; without a
# limit the student would wait forever.
STALL_TIMEOUT_SECONDS = 30.0


class GeminiGenerationProvider:
    """Generate responses, falling back across models on overload or rate limits.

    When the primary model is overloaded or rate limited, the fallback models are tried in
    order, so a demand spike on one model does not fail every question. A model that stops
    responding is treated the same way. A streamed response can only fall back before its
    first chunk arrives.
    """

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "gemini-3.8-flash",
        fallback_models: tuple[str, ...] = (),
        client: genai.Client | None = None,
        stall_timeout: float = STALL_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._models = (model, *(item for item in fallback_models if item != model))
        self._client = client
        self._stall_timeout = stall_timeout

    @staticmethod
    def _config(json_output: bool) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_mime_type="application/json" if json_output else "text/plain"
        )

    def _should_fall_back(self, error: errors.APIError, position: int) -> bool:
        return position < len(self._models) - 1 and error.code in _RETRYABLE_STATUS_CODES

    def _unavailable(self, error: errors.APIError) -> Exception:
        """The error to raise once no model is left to try."""

        if error.code in _RETRYABLE_STATUS_CODES:
            return ProviderUnavailableError(
                f"all Gemini models are overloaded or rate limited (last: {error.code})"
            )
        return error

    async def generate(self, prompt: str, *, json_output: bool = True) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        client = self._get_client()
        for position, model in enumerate(self._models):
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model, contents=prompt, config=self._config(json_output)
                    ),
                    timeout=self._stall_timeout,
                )
            except errors.APIError as error:
                logger.warning("Gemini model %s failed: %s %s", model, error.code, error.status)
                if self._should_fall_back(error, position):
                    continue
                raise self._unavailable(error) from error
            except TimeoutError as error:
                logger.warning(
                    "Gemini model %s sent nothing for %.0f s", model, self._stall_timeout
                )
                if position < len(self._models) - 1:
                    continue
                raise ProviderUnavailableError("Gemini stopped responding") from error
            if response.text is None or not response.text.strip():
                raise RuntimeError("Gemini returned no text response")
            return response.text
        raise RuntimeError("no Gemini generation model is configured")

    async def generate_stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield JSON response text as it is generated."""

        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        client = self._get_client()
        for position, model in enumerate(self._models):
            started = False
            try:
                stream = await asyncio.wait_for(
                    client.aio.models.generate_content_stream(
                        model=model, contents=prompt, config=self._config(True)
                    ),
                    timeout=self._stall_timeout,
                )
                chunks = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            chunks.__anext__(), timeout=self._stall_timeout
                        )
                    except StopAsyncIteration:
                        break
                    if chunk.text:
                        started = True
                        yield chunk.text
            except errors.APIError as error:
                logger.warning("Gemini model %s failed: %s %s", model, error.code, error.status)
                if not started and self._should_fall_back(error, position):
                    continue
                raise self._unavailable(error) from error
            except TimeoutError as error:
                logger.warning(
                    "Gemini model %s sent nothing for %.0f s", model, self._stall_timeout
                )
                if not started and position < len(self._models) - 1:
                    continue
                raise ProviderUnavailableError("Gemini stopped responding") from error
            if not started:
                raise RuntimeError("Gemini returned no text response")
            return
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
