"""Thin asynchronous adapters for the official Google Gen AI Python SDK."""

from google import genai
from google.genai import types
from pydantic import SecretStr

from app.providers.protocols import ProviderConfigurationError


class GeminiEmbeddingProvider:
    """Embed text through Gemini with an explicit output dimensionality."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "gemini-embedding-2",
        dimensions: int = 768,
        client: genai.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._client = client

    @property
    def provider_id(self) -> str:
        return "gemini"

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding input must not be empty")
        client = self._get_client()
        response = await client.aio.models.embed_content(
            model=self._model,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=self._dimensions),
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


class GeminiGenerationProvider:
    """Generate a complete response through the direct Gemini SDK boundary."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model: str = "gemini-3.8-flash",
        client: genai.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    async def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        client = self._get_client()
        response = await client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        if response.text is None or not response.text.strip():
            raise RuntimeError("Gemini returned no text response")
        return response.text

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
