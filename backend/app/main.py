"""FastAPI application and initial readiness contract."""

import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.answering import ConversationTurn, answer_question
from app.config import Settings, get_settings
from app.db import create_database_engine, create_session_factory
from app.ingestion import IngestionError, ingest_pdf, validate_course_id
from app.models import Document
from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.gemini import GeminiEmbeddingProvider, GeminiGenerationProvider
from app.providers.groq import GroqGenerationProvider
from app.providers.openai import OpenAIGenerationProvider
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


class CourseItem(BaseModel):
    """A selectable course and the ingested PDFs that identify it."""

    course_id: str
    source_filenames: list[str]


class CoursesResponse(BaseModel):
    courses: list[CourseItem]


class CourseUploadResponse(BaseModel):
    course_id: str
    source_filename: str
    page_count: int
    chunk_count: int


class StreamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    course_id: StrictStr | None = None
    question: StrictStr | None = None
    history: list[ConversationTurn] = Field(default_factory=list, max_length=12)


MAX_PDF_UPLOAD_BYTES = 25 * 1024 * 1024


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
                if embedding_provider is None:
                    live_providers.append(configured_embedding)
                application.state.embedding_provider = configured_embedding
            else:
                application.state.embedding_provider = embedding_provider

            answer_provider = settings.selected_answer_provider
            if answer_provider == "gemini":
                configured_generation = generation_provider or GeminiGenerationProvider(
                    settings.gemini_api_key, model=settings.answer_model
                )
            elif answer_provider == "openai":
                configured_generation = generation_provider or OpenAIGenerationProvider(
                    settings.openai_api_key, model=settings.openai_answer_model
                )
            elif answer_provider == "groq":
                configured_generation = generation_provider or GroqGenerationProvider(
                    settings.groq_api_key, model=settings.groq_answer_model
                )
            else:
                configured_generation = generation_provider or DeterministicGenerationProvider()
            if generation_provider is None and hasattr(configured_generation, "close"):
                live_providers.append(configured_generation)
            elif generation_provider is None and hasattr(configured_generation, "aclose"):
                live_providers.append(configured_generation)
            application.state.generation_provider = configured_generation
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
                aclose = getattr(provider, "aclose", None)
                if aclose is not None:
                    await aclose()
                    continue
                close = getattr(provider, "close", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result

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

    @application.get("/api/v1/courses", response_model=CoursesResponse, tags=["courses"])
    def list_courses() -> CoursesResponse:
        settings = settings_factory()
        factory = session_factory
        owned_engine: Engine | None = None
        if factory is None:
            owned_engine = engine_factory(settings)
            factory = create_session_factory(owned_engine)
        try:
            with factory() as session:
                rows = session.execute(
                    select(Document.course_id, Document.source_filename).order_by(
                        Document.course_id, Document.source_filename
                    )
                ).all()
            filenames_by_course: dict[str, list[str]] = {}
            for course_id, source_filename in rows:
                filenames_by_course.setdefault(course_id, []).append(source_filename)
            return CoursesResponse(
                courses=[
                    CourseItem(
                        course_id=course_id,
                        source_filenames=sorted(set(filenames)),
                    )
                    for course_id, filenames in filenames_by_course.items()
                ]
            )
        finally:
            if owned_engine is not None:
                owned_engine.dispose()

    @application.post("/api/v1/courses", response_model=CourseUploadResponse, tags=["courses"])
    async def create_course(
        request: Request,
        course_id: str = Query(min_length=1, max_length=200),
        filename: str = Query(min_length=1, max_length=512),
    ) -> CourseUploadResponse:
        try:
            normalized_course_id = validate_course_id(course_id)
        except IngestionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not normalized_course_id.isprintable():
            raise HTTPException(status_code=422, detail="Course name contains invalid characters")

        safe_filename = PurePosixPath(filename.replace("\\", "/")).name
        if (
            not safe_filename
            or safe_filename in {".", ".."}
            or Path(safe_filename).suffix.lower() != ".pdf"
        ):
            raise HTTPException(status_code=422, detail="Choose a PDF file with a .pdf extension")
        if not filename.isprintable():
            raise HTTPException(status_code=422, detail="PDF filename contains invalid characters")

        settings = settings_factory()
        source_root = settings.pdf_source_dir.expanduser().resolve()
        source_root.mkdir(parents=True, exist_ok=True)
        uploaded_bytes = bytearray()
        async for chunk in request.stream():
            if len(uploaded_bytes) + len(chunk) > MAX_PDF_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="PDF must be 25 MB or smaller")
            uploaded_bytes.extend(chunk)
        if not uploaded_bytes:
            raise HTTPException(status_code=422, detail="The uploaded PDF is empty")

        engine: Engine | None = None
        factory = session_factory
        if factory is None:
            engine = engine_factory(settings)
            factory = create_session_factory(engine)
        upload_directory = source_root / ".uploads" / uuid4().hex
        stored_path = upload_directory / safe_filename
        successfully_ingested = False
        try:
            with factory() as session:
                existing_course = session.scalar(
                    select(Document.id).where(Document.course_id == normalized_course_id).limit(1)
                )
            if existing_course is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A course with this name already exists. Choose a different course name.",
                )

            upload_directory.mkdir(parents=True, exist_ok=False)
            stored_path.write_bytes(uploaded_bytes)
            relative_path = stored_path.relative_to(source_root).as_posix()
            selected_embedding_provider = cast(
                EmbeddingProvider,
                embedding_provider
                or getattr(application.state, "embedding_provider", None)
                or DeterministicEmbeddingProvider(settings.embedding_dimensions),
            )
            result = await ingest_pdf(
                source_directory=source_root,
                selected_pdf=relative_path,
                course_id=normalized_course_id,
                session_factory=factory,
                embedding_provider=selected_embedding_provider,
                display_filename=safe_filename,
            )
            successfully_ingested = True
            return CourseUploadResponse(
                course_id=normalized_course_id,
                source_filename=result.source_filename,
                page_count=result.page_count,
                chunk_count=result.chunk_count,
            )
        except HTTPException:
            raise
        except IngestionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail="The PDF could not be ingested. Please try again.",
            ) from error
        finally:
            if not successfully_ingested and stored_path.exists():
                try:
                    stored_path.unlink()
                    upload_directory.rmdir()
                except OSError:
                    pass
            if engine is not None:
                engine.dispose()

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
                answer = await answer_question(
                    question=request.question,
                    course_id=request.course_id,
                    session_factory=factory,
                    embedding_provider=selected_embedding_provider,
                    generation_provider=selected_generation_provider,
                    settings=settings,
                    history=request.history,
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
