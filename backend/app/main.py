"""FastAPI application: course library, document viewer data, conversations, answers."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.answering import (
    AnswerEvent,
    AnswerServiceBusyError,
    Citation,
    Claim,
    CourseSummary,
    DocumentSummary,
    NoEvidenceError,
    Scope,
    UnsupportedQuestionError,
    answer_events,
    list_courses,
    list_documents,
)
from app.config import Settings, get_settings
from app.conversations import (
    ConversationDetail,
    ConversationSummary,
    append_messages,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    start_or_continue,
)
from app.db import create_database_engine, create_session_factory
from app.documents import (
    DocumentContent,
    DocumentInfo,
    DocumentMissingError,
    document_content,
    document_path,
    get_document,
    media_type,
)
from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)
from app.providers.gemini import GeminiGenerationProvider
from app.providers.groq import GroqGenerationProvider
from app.providers.local import LocalEmbeddingProvider
from app.providers.protocols import EmbeddingProvider, GenerationProvider
from app.schema_guard import validate_embedding_schema
from app.stream_contract import (
    AbstentionEvent,
    CitationsEvent,
    ClaimEvent,
    CompletedEvent,
    ConversationEvent,
    ErrorEvent,
    StatusEvent,
)

logger = logging.getLogger(__name__)

ERROR_MESSAGE = "La réponse n'a pas pu être générée. Réessayez dans un instant."
BUSY_MESSAGE = (
    "Le service de rédaction des réponses est saturé (limite de l'offre gratuite Gemini). "
    "Réessayez dans une minute."
)
ABSTENTION_MESSAGE = (
    "Je n'ai pas trouvé cette information dans les supports de cours. Reformulez la "
    "question ou choisissez un autre cours ou document."
)


class ReadinessResponse(BaseModel):
    """Non-sensitive API readiness response."""

    status: Literal["ready"] = "ready"
    service: Literal["course-rag-api"] = "course-rag-api"


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: StrictStr | None = None
    course_id: StrictStr | None = None
    document_id: UUID | None = Field(default=None, strict=False)


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    question: StrictStr
    conversation_id: UUID | None = Field(default=None, strict=False)
    scope: ScopeRequest = Field(default_factory=ScopeRequest)


class CoursesResponse(BaseModel):
    courses: list[CourseSummary]


class DocumentsResponse(BaseModel):
    documents: list[DocumentSummary]


class ConversationsResponse(BaseModel):
    conversations: list[ConversationSummary]


class RenameRequest(BaseModel):
    title: StrictStr


def _line(event: BaseModel) -> str:
    return event.model_dump_json() + "\n"


def _generation_provider(settings: Settings) -> GenerationProvider:
    if settings.answer_provider == "gemini":
        return GeminiGenerationProvider(
            settings.gemini_api_key,
            model=settings.answer_model,
            fallback_models=settings.fallback_answer_models,
        )
    if settings.answer_provider == "groq":
        return GroqGenerationProvider(
            settings.groq_api_key,
            model=settings.answer_model,
            fallback_models=settings.fallback_answer_models,
        )
    return DeterministicGenerationProvider()


def create_app(
    *,
    schema_guard: Callable[[Connection, Settings], None] | None = validate_embedding_schema,
    engine_factory: Callable[[Settings], Engine] = create_database_engine,
    session_factory: sessionmaker[Session] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    generation_provider: GenerationProvider | None = None,
    settings_factory: Callable[[], Settings] = get_settings,
) -> FastAPI:
    """Build the API with injectable storage, providers, and startup schema check."""

    shared: dict[str, Any] = {}

    def sessions() -> sessionmaker[Session]:
        """One engine (connection pool) for the whole process."""

        if session_factory is not None:
            return session_factory
        if "sessions" not in shared:
            shared["engine"] = engine_factory(settings_factory())
            shared["sessions"] = create_session_factory(shared["engine"])
        return cast(sessionmaker[Session], shared["sessions"])

    def providers(settings: Settings) -> tuple[EmbeddingProvider, GenerationProvider]:
        if "embedding" not in shared:
            shared["embedding"] = embedding_provider or (
                LocalEmbeddingProvider(
                    settings.embedding_url,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                )
                if settings.embedding_provider == "local"
                else DeterministicEmbeddingProvider(settings.embedding_dimensions)
            )
            shared["generation"] = generation_provider or _generation_provider(settings)
        return shared["embedding"], shared["generation"]

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncGenerator[None]:
        settings = settings_factory()
        try:
            providers(settings)
            if schema_guard is not None:
                engine = engine_factory(settings)
                try:
                    with engine.connect() as connection:
                        schema_guard(connection, settings)
                finally:
                    engine.dispose()
            yield
        finally:
            generation = shared.get("generation")
            if generation is not None and generation_provider is None:
                aclose = getattr(generation, "aclose", None)
                close = getattr(generation, "close", None)
                if aclose is not None:
                    await aclose()
                elif close is not None:
                    close()
            embedding = shared.get("embedding")
            if embedding is not None and embedding_provider is None:
                aclose = getattr(embedding, "aclose", None)
                if aclose is not None:
                    await aclose()
            if "engine" in shared:
                shared["engine"].dispose()
            shared.clear()

    application = FastAPI(title="Course RAG API", version="0.2.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().frontend_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @application.get("/api/v1/readiness", response_model=ReadinessResponse, tags=["system"])
    def readiness() -> ReadinessResponse:
        return ReadinessResponse()

    @application.get("/api/v1/courses", response_model=CoursesResponse, tags=["library"])
    def courses() -> CoursesResponse:
        return CoursesResponse(courses=list_courses(sessions()))

    @application.get("/api/v1/documents", response_model=DocumentsResponse, tags=["library"])
    def documents(course_id: str | None = Query(default=None)) -> DocumentsResponse:
        return DocumentsResponse(documents=list_documents(sessions(), course_id))

    def _document(document_id: UUID) -> DocumentInfo:
        try:
            return get_document(sessions(), document_id)
        except DocumentMissingError as error:
            raise HTTPException(status_code=404, detail="Document introuvable.") from error

    @application.get(
        "/api/v1/documents/{document_id}", response_model=DocumentInfo, tags=["library"]
    )
    def document(document_id: UUID) -> DocumentInfo:
        return _document(document_id)

    @application.get("/api/v1/documents/{document_id}/file", tags=["library"])
    def document_file(document_id: UUID) -> FileResponse:
        info = _document(document_id)
        try:
            path = document_path(settings_factory().pdf_source_dir, info)
        except DocumentMissingError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return FileResponse(
            path,
            media_type=media_type(info.kind),
            filename=path.name,
            content_disposition_type="inline",
        )

    @application.get(
        "/api/v1/documents/{document_id}/content",
        response_model=DocumentContent,
        tags=["library"],
    )
    def content(document_id: UUID) -> DocumentContent:
        info = _document(document_id)
        try:
            path = document_path(settings_factory().pdf_source_dir, info)
        except DocumentMissingError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return document_content(path, info.kind)

    @application.get(
        "/api/v1/conversations", response_model=ConversationsResponse, tags=["conversations"]
    )
    def conversations() -> ConversationsResponse:
        return ConversationsResponse(conversations=list_conversations(sessions()))

    @application.get(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationDetail,
        tags=["conversations"],
    )
    def conversation(conversation_id: UUID) -> ConversationDetail:
        detail = get_conversation(sessions(), conversation_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
        return detail

    @application.patch(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationSummary,
        tags=["conversations"],
    )
    def rename(conversation_id: UUID, request: RenameRequest) -> ConversationSummary:
        try:
            summary = rename_conversation(sessions(), conversation_id, request.title)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Titre vide.") from error
        if summary is None:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
        return summary

    @application.delete(
        "/api/v1/conversations/{conversation_id}", status_code=204, tags=["conversations"]
    )
    def remove(conversation_id: UUID) -> None:
        if not delete_conversation(sessions(), conversation_id):
            raise HTTPException(status_code=404, detail="Conversation introuvable.")

    @application.post("/api/v1/answers/stream", tags=["answers"])
    async def stream_answer(request: AnswerRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            question = request.question.strip()
            if not question or len(question) > 2000:
                yield _line(ErrorEvent(message="Posez une question (2000 caractères maximum)."))
                return
            settings = settings_factory()
            factory = sessions()
            scope = Scope(
                subject=request.scope.subject,
                course_id=request.scope.course_id,
                document_id=request.scope.document_id,
            )
            try:
                conversation_id, history = start_or_continue(
                    factory, request.conversation_id, question, scope.as_json()
                )
            except Exception:
                logger.exception("could not open the conversation")
                yield _line(ErrorEvent(message=ERROR_MESSAGE))
                return
            yield _line(ConversationEvent(conversation_id=str(conversation_id)))
            yield _line(StatusEvent(stage="searching"))

            claims: list[Claim] = []
            citations: list[Citation] = []
            retrieval_query = question
            status: Literal["answered", "abstained", "error"] = "answered"
            error_message = ERROR_MESSAGE
            embedding, generation = providers(settings)
            try:
                async for event in answer_events(
                    question=question,
                    scope=scope,
                    history=history,
                    session_factory=factory,
                    embedding_provider=embedding,
                    generation_provider=generation,
                    settings=settings,
                ):
                    line = _event_line(event, len(claims), question)
                    if event.kind == "retrieved" and event.retrieval_query:
                        retrieval_query = event.retrieval_query
                    elif event.kind == "claim" and event.claim is not None:
                        claims.append(event.claim)
                    elif event.kind == "citations" and event.citations is not None:
                        citations = event.citations
                    if line:
                        yield line
            except (NoEvidenceError, UnsupportedQuestionError):
                status = "abstained"
            except AnswerServiceBusyError as error:
                logger.warning("answer models are overloaded or rate limited: %s", error.__cause__)
                status, error_message = "error", BUSY_MESSAGE
            except Exception:
                # The client only sees the safe message; the server log keeps the cause.
                logger.exception("answer stream failed")
                status = "error"

            if status == "answered" and not citations:
                status = "error"
            answer_text = (
                " ".join(claim.text for claim in claims)
                if status == "answered"
                else ABSTENTION_MESSAGE
                if status == "abstained"
                else error_message
            )
            payload: dict[str, Any] = {
                "status": status,
                "claims": [claim.model_dump() for claim in claims] if status == "answered" else [],
                "citations": [citation.model_dump() for citation in citations]
                if status == "answered"
                else [],
                "retrieval_query": retrieval_query,
                "scope": scope.as_json(),
            }
            message_id: str | None = None
            try:
                ids = append_messages(
                    factory,
                    conversation_id,
                    [
                        ("user", question, {"scope": scope.as_json()}),
                        ("assistant", answer_text, payload),
                    ],
                )
                message_id = str(ids[-1])
            except Exception:
                logger.exception("could not save the conversation messages")

            if status == "abstained":
                yield _line(AbstentionEvent(message=ABSTENTION_MESSAGE))
            elif status == "error":
                yield _line(ErrorEvent(message=error_message))
            else:
                yield _line(CompletedEvent(message_id=message_id))

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return application


def _event_line(event: AnswerEvent, claim_count: int, question: str) -> str | None:
    if event.kind == "retrieved":
        rewritten = event.retrieval_query if event.retrieval_query != question else None
        return _line(StatusEvent(stage="writing", retrieval_query=rewritten))
    if event.kind == "claim" and event.claim is not None:
        return _line(
            ClaimEvent(index=claim_count, text=event.claim.text, citations=event.claim.citations)
        )
    if event.kind == "citations" and event.citations is not None:
        return _line(CitationsEvent(citations=event.citations))
    return None


app = create_app()
