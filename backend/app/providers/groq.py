"""Answer generation through Groq's OpenAI-compatible chat completions API.

Behaves like the Gemini provider: JSON answers, streaming, and fallback across models
when one is overloaded, rate limited, or stops responding.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import SecretStr

from app.providers.protocols import ProviderConfigurationError, ProviderUnavailableError

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Overload and rate-limit responses that another model may still serve.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Seconds without any response (or between streamed chunks) before a model is considered
# stalled.
STALL_TIMEOUT_SECONDS = 30.0


class _ModelFailedError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status} {detail}")
        self.status = status


class GroqGenerationProvider:
    """Generate responses with Groq, falling back across models on overload or limits."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "openai/gpt-oss-120b",
        fallback_models: tuple[str, ...] = (),
        client: httpx.AsyncClient | None = None,
        stall_timeout: float = STALL_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._models = (model, *(item for item in fallback_models if item != model))
        self._client = client
        self._stall_timeout = stall_timeout

    def _body(self, model: str, prompt: str, *, json_output: bool, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if json_output:
            body["response_format"] = {"type": "json_object"}
        return body

    def _can_fall_back(self, error: Exception, position: int) -> bool:
        if position >= len(self._models) - 1:
            return False
        return isinstance(error, TimeoutError) or (
            isinstance(error, _ModelFailedError) and error.status in _RETRYABLE_STATUS_CODES
        )

    @staticmethod
    def _final_error(error: Exception) -> Exception:
        """The error to raise once no model is left to try."""

        if isinstance(error, TimeoutError):
            return ProviderUnavailableError("Groq stopped responding")
        if isinstance(error, _ModelFailedError) and error.status in _RETRYABLE_STATUS_CODES:
            return ProviderUnavailableError(
                f"all Groq models are overloaded or rate limited (last: {error.status})"
            )
        return RuntimeError(f"Groq request failed: {error}")

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            await response.aread()
            raise _ModelFailedError(response.status_code, response.text[:300])

    async def generate(self, prompt: str, *, json_output: bool = True) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        client = self._get_client()
        for position, model in enumerate(self._models):
            try:
                response = await asyncio.wait_for(
                    client.post(
                        GROQ_URL,
                        json=self._body(model, prompt, json_output=json_output, stream=False),
                    ),
                    timeout=self._stall_timeout,
                )
                await self._raise_for_status(response)
            except (TimeoutError, _ModelFailedError) as error:
                logger.warning("Groq model %s failed: %s", model, str(error) or "no response")
                if self._can_fall_back(error, position):
                    continue
                raise self._final_error(error) from error
            text = response.json()["choices"][0]["message"].get("content") or ""
            if not text.strip():
                raise RuntimeError("Groq returned no text response")
            return str(text)
        raise RuntimeError("no Groq generation model is configured")

    async def generate_stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield JSON response text as it is generated."""

        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        client = self._get_client()
        for position, model in enumerate(self._models):
            started = False
            try:
                request = client.build_request(
                    "POST", GROQ_URL, json=self._body(model, prompt, json_output=True, stream=True)
                )
                response = await asyncio.wait_for(
                    client.send(request, stream=True), timeout=self._stall_timeout
                )
                try:
                    await self._raise_for_status(response)
                    lines = response.aiter_lines().__aiter__()
                    while True:
                        try:
                            line = await asyncio.wait_for(
                                lines.__anext__(), timeout=self._stall_timeout
                            )
                        except StopAsyncIteration:
                            break
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        event = json.loads(data)
                        if "error" in event:
                            raise RuntimeError(f"Groq stream error: {event['error']}")
                        choices = event.get("choices") or [{}]
                        piece = (choices[0].get("delta") or {}).get("content")
                        if piece:
                            started = True
                            yield piece
                finally:
                    await response.aclose()
            except (TimeoutError, _ModelFailedError) as error:
                logger.warning("Groq model %s failed: %s", model, str(error) or "no response")
                if not started and self._can_fall_back(error, position):
                    continue
                raise self._final_error(error) from error
            if not started:
                raise RuntimeError("Groq returned no text response")
            return
        raise RuntimeError("no Groq generation model is configured")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._api_key is None or not self._api_key.get_secret_value():
            raise ProviderConfigurationError(
                "GROQ_API_KEY is required when the Groq provider is invoked"
            )
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            timeout=httpx.Timeout(None, connect=10.0),
        )
        return self._client

    async def aclose(self) -> None:
        """Release the HTTP connections, if a client was created."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
