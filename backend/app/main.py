"""FastAPI application and initial readiness contract."""

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Literal, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StrictStr
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.answering import (
    CourseSummary,
    NoEvidenceError,
    UnsupportedQuestionError,
    answer_question,
    list_courses,
)
from app.config import Settings, get_settings
from app.db import create_database_engine, create_session_factory
from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.gemini import GeminiEmbeddingProvider, GeminiGenerationProvider
from app.providers.protocols import EmbeddingProvider, GenerationProvider
from app.schema_guard import validate_embedding_schema
from app.stream_contract import (
    AbstentionEvent,
    CitationItem,
    CitationsEvent,
    CompletedEvent,
    DeltaEvent,
    ErrorEvent,
)

logger = logging.getLogger(__name__)


class ReadinessResponse(BaseModel):
    """Non-sensitive API readiness response."""

    status: Literal["ready"] = "ready"
    service: Literal["course-rag-api"] = "course-rag-api"


class StreamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    # Omitted or null scopes search every course; a string must name one subject/course.
    subject: StrictStr | None = None
    course_id: StrictStr | None = None
    question: StrictStr | None = None


class CoursesResponse(BaseModel):
    """Courses that currently have ingested material."""

    courses: list[CourseSummary]


_ERROR_MESSAGE = "The answer could not be generated. Check the course and question, then try again."
_ABSTENTION_MESSAGE = (
    "I couldn't find this in the course material. Try rephrasing the question "
    "or choosing a different course."
)


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
        settings = settings_factory()
        live_providers: list[object] = []
        try:
            if settings.rag_provider == "gemini":
                configured_embedding = embedding_provider or GeminiEmbeddingProvider(
                    settings.gemini_api_key,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                )
                configured_generation = (
                    generation_provider
                    or GeminiGenerationProvider(
                        settings.gemini_api_key,
                        model=settings.answer_model,
                        fallback_models=settings.fallback_answer_models,
                    )
                )
                # Validate credentials at startup without making a provider request.
                if (
                    settings.gemini_api_key is None
                    or not settings.gemini_api_key.get_secret_value()
                ):
                    raise ValueError("GEMINI_API_KEY is required when RAG_PROVIDER=gemini")
                if embedding_provider is None:
                    live_providers.append(configured_embedding)
                if generation_provider is None:
                    live_providers.append(configured_generation)
                application.state.embedding_provider = configured_embedding
                application.state.generation_provider = configured_generation
            else:
                application.state.embedding_provider = embedding_provider
                application.state.generation_provider = generation_provider
            if schema_guard is not None:
                engine = engine_factory(settings)
                try:
                    with engine.connect() as connection:
                        schema_guard(connection, settings)
                finally:
                    engine.dispose()
            yield
        finally:
            for provider in live_providers:
                close = getattr(provider, "close", None)
                if close is not None:
                    close()

    application = FastAPI(title="Local Course RAG API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().frontend_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @contextmanager
    def session_scope(settings: Settings) -> Iterator[sessionmaker[Session]]:
        """Yield the injected session factory, or one backed by a request-owned engine."""

        if session_factory is not None:
            yield session_factory
            return
        owned_engine = engine_factory(settings)
        try:
            yield create_session_factory(owned_engine)
        finally:
            owned_engine.dispose()

    @application.get("/api/v1/readiness", response_model=ReadinessResponse, tags=["system"])
    def readiness() -> ReadinessResponse:
        return ReadinessResponse()

    @application.get("/api/v1/courses", response_model=CoursesResponse, tags=["courses"])
    def courses() -> CoursesResponse:
        with session_scope(settings_factory()) as factory:
            return CoursesResponse(courses=list_courses(factory))

    @application.post("/api/v1/answers/stream", tags=["answers"])
    async def stream_answer(request: StreamRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            terminal = False
            try:
                settings = settings_factory()
                if (
                    (request.course_id is not None and not request.course_id.strip())
                    or (request.subject is not None and not request.subject.strip())
                    or not request.question
                    or not request.question.strip()
                ):
                    yield ErrorEvent(message=_ERROR_MESSAGE).model_dump_json() + "\n"
                    terminal = True
                    return
                selected_embedding_provider = cast(
                    EmbeddingProvider,
                    embedding_provider
                    or getattr(application.state, "embedding_provider", None)
                    or DeterministicEmbeddingProvider(settings.embedding_dimensions),
                )
                selected_generation_provider = cast(
                    GenerationProvider,
                    generation_provider
                    or getattr(application.state, "generation_provider", None)
                    or DeterministicGenerationProvider(),
                )
                try:
                    with session_scope(settings) as factory:
                        answer = await answer_question(
                            question=request.question,
                            course_id=request.course_id,
                            subject=request.subject,
                            session_factory=factory,
                            embedding_provider=selected_embedding_provider,
                            generation_provider=selected_generation_provider,
                            settings=settings,
                        )
                except (NoEvidenceError, UnsupportedQuestionError):
                    yield AbstentionEvent(message=_ABSTENTION_MESSAGE).model_dump_json() + "\n"
                    terminal = True
                    return
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
                # The client only sees the safe message; the server log keeps the cause.
                logger.exception("answer stream failed")
                if not terminal:
                    yield ErrorEvent(message=_ERROR_MESSAGE).model_dump_json() + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return application


app = create_app()
