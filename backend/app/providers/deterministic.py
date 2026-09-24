"""Repeatable provider doubles for offline development and tests."""

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class DeterministicEmbeddingProvider:
    """Create stable bounded vectors without network access."""

    dimensions: int = 768

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding input must not be empty")
        seed = text.encode("utf-8")
        values: list[float] = []
        for block in range((self.dimensions + 31) // 32):
            digest = sha256(seed + block.to_bytes(4, "big")).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
        return values[: self.dimensions]


@dataclass(frozen=True)
class DeterministicGenerationProvider:
    """Return a configured response, independent of network or model state."""

    response: str = "deterministic response"

    async def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        return self.response
