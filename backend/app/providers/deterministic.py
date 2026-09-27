"""Repeatable provider doubles for offline development and tests."""

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256

# Common French and English function words that would otherwise dominate lexical overlap.
_STOPWORDS = frozenset(
    """
    a an and are as at be by do does for from how in is it of on or that the this to was what
    when where which who why will with can about into its than then there these those
    au aux avec ce ces cet cette dans de des du elle en est et il ils je la le les leur lui
    ma mais me mes mon ne nous on ou par pas pour qu que qui sa se ses son sont sur ta te
    tes ton tu un une vos votre vous comment quel quelle quels quelles quoi est-ce
    """.split()
)


def _terms(text: str) -> list[str]:
    """Lowercase, strip accents, and keep informative word tokens."""

    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return [
        token
        for token in re.findall(r"\w+", folded)
        if len(token) > 1 and token not in _STOPWORDS
    ]


@dataclass(frozen=True)
class DeterministicEmbeddingProvider:
    """Create stable lexical vectors without network access.

    Terms are hashed into a fixed number of signed buckets (the hashing trick) with
    sublinear term frequency, then L2-normalized, so cosine similarity reflects shared
    vocabulary. This keeps the offline demo useful without semantic understanding.
    """

    dimensions: int = 768

    @property
    def provider_id(self) -> str:
        return "deterministic"

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding input must not be empty")
        counts: dict[str, int] = {}
        for term in _terms(text):
            counts[term] = counts.get(term, 0) + 1
        values = [0.0] * self.dimensions
        for term, count in counts.items():
            digest = sha256(term.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            values[bucket] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0:
            # Text made only of stopwords or symbols still gets a stable nonzero vector.
            digest = sha256(text.encode("utf-8")).digest()
            values[int.from_bytes(digest[:4], "big") % self.dimensions] = 1.0
            return values
        return [value / norm for value in values]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


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
