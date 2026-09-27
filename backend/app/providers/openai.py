"""Asynchronous OpenAI answer generation adapter."""

from openai import AsyncOpenAI
from pydantic import SecretStr

from app.providers.protocols import ProviderConfigurationError


class OpenAIGenerationProvider:
    """Generate JSON answer claims through OpenAI's Responses API."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "gpt-4.1-mini",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    async def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        response = await self._get_client().responses.create(
            model=self._model,
            input=prompt,
            text={"format": {"type": "json_object"}},
        )
        if not response.output_text or not response.output_text.strip():
            raise RuntimeError("OpenAI returned no text response")
        return response.output_text

    def _get_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        if self._api_key is None or not self._api_key.get_secret_value():
            raise ProviderConfigurationError(
                "OPENAI_API_KEY is required when the OpenAI provider is invoked"
            )
        self._client = AsyncOpenAI(
            api_key=self._api_key.get_secret_value(),
            max_retries=2,
            timeout=30.0,
        )
        return self._client

    async def aclose(self) -> None:
        """Release the async SDK client's HTTP resources, if created."""
        if self._client is not None:
            await self._client.close()
            self._client = None
