"""Asynchronous Groq answer generation through its OpenAI-compatible API."""

from openai import AsyncOpenAI
from pydantic import SecretStr

from app.providers.protocols import ProviderConfigurationError


class GroqGenerationProvider:
    """Generate JSON answer claims through Groq's chat completions endpoint."""

    _BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "openai/gpt-oss-20b",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    async def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        response = await self._get_client().chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        if not response.choices:
            raise RuntimeError("Groq returned no completion choices")
        content = response.choices[0].message.content
        if content is None or not content.strip():
            raise RuntimeError("Groq returned no text response")
        return content

    def _get_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        if self._api_key is None or not self._api_key.get_secret_value():
            raise ProviderConfigurationError(
                "GROQ_API_KEY is required when the Groq provider is invoked"
            )
        self._client = AsyncOpenAI(
            api_key=self._api_key.get_secret_value(),
            base_url=self._BASE_URL,
            max_retries=2,
            timeout=30.0,
        )
        return self._client

    async def aclose(self) -> None:
        """Release the async SDK client's HTTP resources, if created."""
        if self._client is not None:
            await self._client.close()
            self._client = None
