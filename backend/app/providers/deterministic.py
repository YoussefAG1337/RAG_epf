"""Repeatable provider doubles for offline development and tests."""

import json
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
    """Return a repeatable evidence-backed JSON response without network access."""

    response: str | None = None

    async def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("generation prompt must not be empty")
        if self.response is not None:
            return self.response
        marker = "Evidence:\n"
        if marker not in prompt:
            return json.dumps({"claims": []})
        try:
            excerpts = json.loads(prompt.rsplit(marker, maxsplit=1)[1])
            first = excerpts[0]
            citation_id = first["citation_id"]
            excerpt = first["excerpt"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            return json.dumps({"claims": []})
        return json.dumps(
            {"claims": [{"text": excerpt, "citation_ids": [citation_id]}]},
            ensure_ascii=False,
        )
