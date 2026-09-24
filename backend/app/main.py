"""FastAPI application and initial readiness contract."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StrictStr
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.answering import answer_question
from app.config import Settings, get_settings
from app.db import create_database_engine, create_session_factory
from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.protocols import EmbeddingProvider, GenerationProvider
from app.schema_guard import validate_embedding_schema
from app.stream_contract import (
    CitationItem,
    CitationsEvent,
    CompletedEvent,
    DeltaEvent,
    ErrorEvent,
)


class ReadinessResponse(BaseModel):
    """Non-sensitive API readiness response."""

    status: Literal["ready"] = "ready"
    service: Literal["course-rag-api"] = "course-rag-api"


class StreamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    course_id: StrictStr | None = None
    question: StrictStr | None = None


def _delta_chunks(text: str, maximum_length: int = 80) -> list[str]:
    """Split validated claim text into nonempty word-aware pieces without loss."""

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + maximum_length, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end])
        start = end
    return chunks


def create_app(
    *,
    schema_guard: Callable[[Connection, Settings], None] | None = validate_embedding_schema,
    engine_factory: Callable[[Settings], Engine] = create_database_engine,
    session_factory: sessionmaker[Session] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    generation_provider: GenerationProvider | None = None,
    settings_factory: Callable[[], Settings] = get_settings,
) -> FastAPI:
    """Build the API application with an injectable startup schema check."""

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        if schema_guard is not None:
            settings = settings_factory()
            engine = engine_factory(settings)
            try:
                with engine.connect() as connection:
                    schema_guard(connection, settings)
            finally:
                engine.dispose()
        yield

    application = FastAPI(title="Local Course RAG API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[get_settings().frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @application.get("/api/v1/readiness", response_model=ReadinessResponse, tags=["system"])
    def readiness() -> ReadinessResponse:
        return ReadinessResponse()

    @application.post("/api/v1/answers/stream", tags=["answers"])
    async def stream_answer(request: StreamRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            terminal = False
            owned_engine: Engine | None = None
            try:
                settings = settings_factory()
                if (
                    not request.course_id
                    or not request.course_id.strip()
                    or not request.question
                    or not request.question.strip()
                ):
                    yield ErrorEvent(
                        message=(
                            "The answer could not be generated. Check the course and "
                            "question, then try again."
                        )
                    ).model_dump_json() + "\n"
                    terminal = True
                    return
                factory = session_factory
                if factory is None:
                    owned_engine = engine_factory(settings)
                    factory = create_session_factory(owned_engine)
                answer = await answer_question(
                    question=request.question,
                    course_id=request.course_id,
                    session_factory=factory,
                    embedding_provider=embedding_provider
                    or DeterministicEmbeddingProvider(settings.embedding_dimensions),
                    generation_provider=generation_provider or DeterministicGenerationProvider(),
                    settings=settings,
                )
                for claim_index, claim in enumerate(answer.claims):
                    if claim_index:
                        yield DeltaEvent(text=" ").model_dump_json() + "\n"
                    for chunk in _delta_chunks(claim.text):
                        yield DeltaEvent(text=chunk).model_dump_json() + "\n"
                citations = [
                    CitationItem(**citation.model_dump()) for citation in answer.citations
                ]
                yield CitationsEvent(citations=citations).model_dump_json() + "\n"
                yield CompletedEvent().model_dump_json() + "\n"
                terminal = True
            except Exception:
                if not terminal:
                    message = (
                        "The answer could not be generated. Check the course and "
                        "question, then try again."
                    )
                    yield ErrorEvent(message=message).model_dump_json() + "\n"
            finally:
                if owned_engine is not None:
                    owned_engine.dispose()

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return application


app = create_app()
