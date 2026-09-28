"""Local embedding service: one sentence-transformers model behind a small HTTP API.

Retrieval models expect questions and passages to be encoded differently (an instruction
or a "query:"/"passage:" prefix). Callers send ``kind`` and the service applies the
convention of the configured model, so the rest of the system never has to know it.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
MAX_SEQUENCE_LENGTH = int(os.environ.get("EMBEDDING_MAX_TOKENS", "1024"))
# "auto" picks an NVIDIA GPU, then an Apple GPU (Metal), then the CPU.
DEVICE = os.environ.get("EMBEDDING_DEVICE", "auto")
BATCH_SIZE_OVERRIDE = os.environ.get("EMBEDDING_BATCH_SIZE")


@dataclass(frozen=True)
class Convention:
    query_prefix: str = ""
    document_prefix: str = ""
    query_prompt_name: str | None = None
    document_prompt_name: str | None = None


# How each supported model family wants queries and documents encoded.
CONVENTIONS: dict[str, Convention] = {
    "BAAI/bge-m3": Convention(),
    "Qwen/Qwen3-Embedding-0.6B": Convention(query_prompt_name="query"),
    "intfloat/multilingual-e5-large-instruct": Convention(
        query_prefix=(
            "Instruct: Given a student question, retrieve course passages that answer it\n"
            "Query: "
        )
    ),
    "intfloat/multilingual-e5-base": Convention(
        query_prefix="query: ", document_prefix="passage: "
    ),
    "google/embeddinggemma-300m": Convention(
        query_prompt_name="Retrieval-query", document_prompt_name="Retrieval-document"
    ),
}


class EmbedRequest(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=256)
    kind: Literal["query", "document"]


class EmbedResponse(BaseModel):
    model: str
    dimensions: int
    embeddings: list[list[float]]


class ModelInfo(BaseModel):
    model: str
    dimensions: int
    device: str
    ready: bool


class _State:
    model: Any = None
    dimensions: int = 0
    device: str = "cpu"
    batch_size: int = 16
    # Encoding saturates the device and the model is not re-entrant; serialize requests.
    lock = threading.Lock()


state = _State()


def _pick_device() -> str:
    import torch

    if DEVICE != "auto":
        return DEVICE
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load() -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    device = _pick_device()
    # GPUs run float16 at full speed with near-identical vectors (cosine >= 0.9998 on the
    # course material). CPUs need float32: some models ship bfloat16 weights, which CPUs
    # compute ~30x slower (18 min vs 62 s for the course material with Qwen3-Embedding).
    dtype = torch.float32 if device == "cpu" else torch.float16
    model = SentenceTransformer(
        MODEL_NAME,
        device=device,
        trust_remote_code=True,
        model_kwargs={"torch_dtype": dtype},
    )
    model.max_seq_length = MAX_SEQUENCE_LENGTH
    state.model = model
    state.device = device
    # Small batches pad less; on Apple GPUs batch 8 was fastest (31 s vs 50 s at 64).
    state.batch_size = int(BATCH_SIZE_OVERRIDE or (8 if device == "mps" else 16))
    dimension = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    state.dimensions = int(dimension() or 0)


def encode(texts: list[str], kind: Literal["query", "document"]) -> list[list[float]]:
    convention = CONVENTIONS.get(MODEL_NAME, Convention())
    prefix = convention.query_prefix if kind == "query" else convention.document_prefix
    prompt_name = (
        convention.query_prompt_name
        if kind == "query"
        else convention.document_prompt_name
    )
    keyword: dict[str, Any] = {"prompt_name": prompt_name} if prompt_name else {}
    with state.lock:
        vectors = state.model.encode(
            [prefix + text for text in texts],
            batch_size=state.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            **keyword,
        )
    return [[float(value) for value in vector] for vector in vectors]


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    _load()
    yield


app = FastAPI(title="Course RAG embeddings", lifespan=lifespan)


@app.get("/health", response_model=ModelInfo)
def health() -> ModelInfo:
    return ModelInfo(
        model=MODEL_NAME,
        dimensions=state.dimensions,
        device=state.device,
        ready=state.model is not None,
    )


@app.post("/embed", response_model=EmbedResponse)
def embed(request: EmbedRequest) -> EmbedResponse:
    if state.model is None:
        raise HTTPException(status_code=503, detail="model is still loading")
    if any(not text.strip() for text in request.inputs):
        raise HTTPException(status_code=422, detail="inputs must not be empty")
    return EmbedResponse(
        model=MODEL_NAME,
        dimensions=state.dimensions,
        embeddings=encode(request.inputs, request.kind),
    )
