"""Scoped retrieval and grounded, quote-verified answer generation.

The model receives numbered excerpts (S1, S2, ...) and must answer with short claims, each
citing excerpts by label together with a verbatim quote. Quotes are checked against the
excerpt and mapped to the exact source lines, so the interface can highlight them.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingestion import validate_course_id, validate_subject
from app.locating import find_quote, highlights
from app.models import Document, DocumentChunk
from app.providers.protocols import (
    EmbeddingProvider,
    GenerationProvider,
    ProviderUnavailableError,
)

EXCERPT_MARKER = "Extraits :\n"
QUESTION_MARKER = "Dernière question :\n"


class AnsweringError(RuntimeError):
    """A question could not be answered safely from the selected material."""


class NoEvidenceError(AnsweringError):
    """No sufficiently relevant evidence exists in the requested scope."""


class UnsupportedQuestionError(AnsweringError):
    """Evidence was retrieved, but it does not support an answer."""


class InvalidGroundedResponseError(AnsweringError):
    """The model response was not valid claims JSON."""


class AnswerServiceBusyError(AnsweringError):
    """Every answer model is overloaded or rate limited; asking again later may work."""


@dataclass(frozen=True)
class Scope:
    """Where to search; all ``None`` means every course."""

    subject: str | None = None
    course_id: str | None = None
    document_id: UUID | None = None

    def normalized(self) -> Scope:
        return Scope(
            subject=None if self.subject is None else validate_subject(self.subject),
            course_id=None if self.course_id is None else validate_course_id(self.course_id),
            document_id=self.document_id,
        )

    def as_json(self) -> dict[str, str]:
        values = {
            "subject": self.subject,
            "course_id": self.course_id,
            "document_id": None if self.document_id is None else str(self.document_id),
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class HistoryTurn:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Evidence:
    label: int  # S1, S2, ... in rank order
    chunk_id: UUID
    document_id: UUID
    subject: str
    course_id: str
    document_title: str | None
    section_title: str | None
    source_filename: str
    page: int
    excerpt: str
    score: float
    locations: list[dict[str, Any]] = field(default_factory=list)


class Highlight(BaseModel):
    """Where to highlight on one page (PDF boxes as page fractions) or which file lines."""

    page: int
    boxes: list[list[float]]
    lines: list[int]


class Citation(BaseModel):
    """An excerpt cited by the answer, with the verified quotes and where they are."""

    number: int
    chunk_id: str
    document_id: str
    subject: str
    course_id: str
    document_title: str | None
    section_title: str | None
    source_filename: str
    page: int
    excerpt: str
    quotes: list[str]
    highlights: list[Highlight]


class Claim(BaseModel):
    """One sentence or two of the answer and the citation numbers supporting it."""

    text: str
    citations: list[int]


class GroundedAnswer(BaseModel):
    claims: list[Claim]
    citations: list[Citation]
    retrieval_query: str


class CourseSummary(BaseModel):
    """A course that has ingested material available for questions."""

    subject: str
    course_id: str
    document_count: int
    page_count: int


class DocumentSummary(BaseModel):
    id: str
    subject: str
    course_id: str
    title: str
    source_filename: str
    page_count: int


def _retrieve(
    *,
    session_factory: sessionmaker[Session],
    scope: Scope,
    query_embedding: list[float],
    limit: int,
    minimum_score: float,
) -> list[Evidence]:
    """Filter to the scope in SQL before cosine ranking and limiting."""

    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    statement = select(
        DocumentChunk.id,
        DocumentChunk.document_id,
        DocumentChunk.course_id,
        DocumentChunk.physical_page_number,
        DocumentChunk.section_title,
        DocumentChunk.text,
        DocumentChunk.locations,
        Document.subject,
        Document.title,
        Document.source_filename,
        distance.label("distance"),
    ).join(
        Document,
        (Document.id == DocumentChunk.document_id)
        & (Document.course_id == DocumentChunk.course_id),
    )
    if scope.course_id is not None:
        statement = statement.where(
            DocumentChunk.course_id == scope.course_id, Document.course_id == scope.course_id
        )
    if scope.subject is not None:
        statement = statement.where(Document.subject == scope.subject)
    if scope.document_id is not None:
        statement = statement.where(DocumentChunk.document_id == scope.document_id)
    statement = statement.order_by(distance).limit(limit)
    with session_factory() as session:
        rows = session.execute(statement).all()

    evidence: list[Evidence] = []
    for row in rows:
        score = 1.0 - float(row.distance)
        if not math.isfinite(score) or score < minimum_score:
            continue
        evidence.append(
            Evidence(
                label=len(evidence) + 1,
                chunk_id=row.id,
                document_id=row.document_id,
                subject=row.subject,
                course_id=row.course_id,
                document_title=row.title,
                section_title=row.section_title,
                source_filename=row.source_filename,
                page=row.physical_page_number,
                excerpt=row.text,
                score=score,
                locations=list(row.locations or []),
            )
        )
    return evidence


def _history_text(history: list[HistoryTurn]) -> str:
    lines = []
    for turn in history:
        speaker = "Étudiant" if turn.role == "user" else "Assistant"
        lines.append(f"{speaker} : {turn.content.strip()}")
    return "\n".join(lines)


def condensation_prompt(question: str, history: list[HistoryTurn]) -> str:
    return (
        "Voici le début d'une conversation entre un étudiant et l'assistant de ses cours, "
        "puis la dernière question de l'étudiant. Réécris cette dernière question pour "
        "qu'elle soit compréhensible seule (remplace « il », « ça », « et pour … » par ce "
        "qu'ils désignent), dans la même langue. Réponds uniquement par la question "
        "réécrite, sans guillemets.\n\n"
        f"Conversation :\n{_history_text(history)}\n\n{QUESTION_MARKER}{question}"
    )


def answer_prompt(question: str, history: list[HistoryTurn], evidence: list[Evidence]) -> str:
    """Build a prompt whose only course facts are the retrieved excerpts."""

    excerpts = json.dumps(
        [
            {
                "id": f"S{item.label}",
                "cours": item.course_id,
                "document": item.document_title or item.source_filename,
                "section": item.section_title,
                "page": item.page,
                "extrait": item.excerpt,
            }
            for item in evidence
        ],
        ensure_ascii=False,
    )
    conversation = (
        "Conversation précédente (pour comprendre la question, pas comme source de faits) :\n"
        f"{_history_text(history)}\n\n"
        if history
        else ""
    )
    return (
        "Tu es l'assistant de cours d'une école d'ingénieurs. Réponds à la question de "
        "l'étudiant uniquement avec les informations des extraits de cours ci-dessous "
        "(diapositives, TD, QCM, fiches). Les diapositives sont concises : relie les "
        "extraits en phrases complètes et claires, sans ajouter de fait absent des extraits.\n"
        "Donne une réponse complète, en 2 à 6 affirmations quand les extraits le permettent, "
        "en combinant tous les extraits pertinents. Pour expliquer une notion, appuie-toi "
        "d'abord sur les cours et les fiches : un énoncé d'exercice (TD) pose une question, "
        "il ne l'explique pas ; cite-le quand l'étudiant demande un exercice. Une réponse de "
        "QCM marquée « ✔ (réponse surlignée) » est la bonne réponse.\n"
        "Réponds en JSON avec exactement cette forme : "
        '{"claims":[{"text":"une ou deux phrases","sources":[{"id":"S1",'
        '"quote":"passage copié mot pour mot de l\'extrait S1 (5 à 25 mots)"}]}]}.\n'
        "Chaque affirmation cite au moins un extrait ; la citation (quote) doit être "
        "recopiée exactement depuis cet extrait. N'invente ni fait, ni source, ni citation. "
        'Si les extraits ne permettent pas de répondre, renvoie {"claims":[]}. '
        "Écris les affirmations dans la langue de la question.\n\n"
        f"{conversation}Question :\n{question}\n\n{EXCERPT_MARKER}{excerpts}"
    )


class _ClaimStreamParser:
    """Extract complete claim objects from a streamed ``{"claims": [...]}`` response."""

    def __init__(self) -> None:
        self._buffer = ""
        self._position = 0
        self._in_array = False
        self.finished = False

    def feed(self, text: str) -> list[dict[str, Any]]:
        self._buffer += text
        claims: list[dict[str, Any]] = []
        if not self._in_array:
            key = self._buffer.find('"claims"')
            bracket = self._buffer.find("[", key) if key >= 0 else -1
            if bracket < 0:
                return claims
            self._in_array, self._position = True, bracket + 1
        while not self.finished:
            start = self._position
            while start < len(self._buffer) and self._buffer[start] in " \t\r\n,":
                start += 1
            if start >= len(self._buffer):
                return claims
            if self._buffer[start] == "]":
                self.finished = True
                break
            if self._buffer[start] != "{":
                raise InvalidGroundedResponseError("claims array contains a non-object")
            end = self._object_end(start)
            if end is None:
                return claims
            try:
                parsed = json.loads(self._buffer[start:end])
            except json.JSONDecodeError as error:
                raise InvalidGroundedResponseError("claim is not valid JSON") from error
            if isinstance(parsed, dict):
                claims.append(parsed)
            self._position = end
        return claims

    def _object_end(self, start: int) -> int | None:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(self._buffer)):
            character = self._buffer[index]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
            elif character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return None


class _CitationBook:
    """Numbers cited excerpts in order of first use and collects their verified quotes."""

    def __init__(self, evidence: list[Evidence]) -> None:
        self._by_label = {item.label: item for item in evidence}
        self._numbers: dict[int, int] = {}
        self._spans: dict[int, list[tuple[int, int]]] = {}
        self._quotes: dict[int, list[str]] = {}

    def claim(self, raw: dict[str, Any]) -> Claim | None:
        text = raw.get("text")
        sources = raw.get("sources")
        if not isinstance(text, str) or not text.strip() or not isinstance(sources, list):
            return None
        numbers: list[int] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            label = str(source.get("id", "")).strip().upper().removeprefix("S")
            if not label.isdigit() or int(label) not in self._by_label:
                continue  # an unknown source is dropped, never shown
            item = self._by_label[int(label)]
            number = self._numbers.setdefault(item.label, len(self._numbers) + 1)
            quote = source.get("quote")
            if isinstance(quote, str) and quote.strip():
                span = find_quote(item.excerpt, quote)
                if span is not None:
                    self._spans.setdefault(item.label, []).append(span)
                    self._quotes.setdefault(item.label, []).append(item.excerpt[span[0] : span[1]])
            if number not in numbers:
                numbers.append(number)
        if not numbers:
            return None  # a claim without a valid source is not shown
        return Claim(text=text.strip(), citations=numbers)

    def citations(self) -> list[Citation]:
        result: list[Citation] = []
        for label, number in sorted(self._numbers.items(), key=lambda pair: pair[1]):
            item = self._by_label[label]
            spans = self._spans.get(label)
            marked: list[dict[str, Any]] = []
            if spans:
                for span in spans:
                    marked.extend(highlights(item.locations, span))
            else:
                marked = highlights(item.locations, None)  # no verified quote: whole excerpt
            merged: dict[int, Highlight] = {}
            for entry in marked:
                page = merged.setdefault(
                    entry["page"], Highlight(page=entry["page"], boxes=[], lines=[])
                )
                page.boxes.extend(box for box in entry["boxes"] if box not in page.boxes)
                page.lines.extend(line for line in entry["lines"] if line not in page.lines)
            result.append(
                Citation(
                    number=number,
                    chunk_id=str(item.chunk_id),
                    document_id=str(item.document_id),
                    subject=item.subject,
                    course_id=item.course_id,
                    document_title=item.document_title,
                    section_title=item.section_title,
                    source_filename=item.source_filename,
                    page=item.page,
                    excerpt=item.excerpt,
                    quotes=self._quotes.get(label, []),
                    highlights=sorted(merged.values(), key=lambda value: value.page),
                )
            )
        return result


@dataclass
class AnswerEvent:
    kind: Literal["retrieved", "claim", "citations"]
    claim: Claim | None = None
    citations: list[Citation] | None = None
    retrieval_query: str | None = None


async def _condense(
    question: str, history: list[HistoryTurn], generation_provider: GenerationProvider
) -> str:
    if not history:
        return question
    try:
        rewritten = await generation_provider.generate(
            condensation_prompt(question, history), json_output=False
        )
    except Exception:
        return question  # searching with the raw question beats failing
    rewritten = rewritten.strip().strip('"«» ').strip()
    return rewritten if 0 < len(rewritten) <= 500 else question


async def answer_events(
    *,
    question: str,
    scope: Scope,
    history: list[HistoryTurn],
    session_factory: sessionmaker[Session],
    embedding_provider: EmbeddingProvider,
    generation_provider: GenerationProvider,
    settings: Settings,
) -> AsyncIterator[AnswerEvent]:
    """Yield the search query, then each validated claim as it is generated, then the
    citations. Raises NoEvidenceError/UnsupportedQuestionError when it should abstain."""

    normalized_question = question.strip()
    if not normalized_question:
        raise AnsweringError("question must not be empty")
    normalized_scope = scope.normalized()
    history = history[-settings.conversation_history_messages :]

    retrieval_query = await _condense(normalized_question, history, generation_provider)
    try:
        query_embedding = await embedding_provider.embed(retrieval_query)
    except Exception as error:
        raise AnsweringError("question embedding failed") from error
    if len(query_embedding) != settings.embedding_dimensions or not all(
        math.isfinite(value) for value in query_embedding
    ):
        raise AnsweringError("question embedding has an unexpected shape")

    evidence = _retrieve(
        session_factory=session_factory,
        scope=normalized_scope,
        query_embedding=query_embedding,
        limit=settings.retrieval_limit,
        minimum_score=settings.minimum_score,
    )
    if not evidence:
        raise NoEvidenceError("no sufficiently relevant evidence was found")
    yield AnswerEvent(kind="retrieved", retrieval_query=retrieval_query)

    prompt = answer_prompt(normalized_question, history, evidence)
    book = _CitationBook(evidence)
    parser = _ClaimStreamParser()
    emitted = 0
    stream = getattr(generation_provider, "generate_stream", None)
    try:
        if stream is not None:
            async for text in stream(prompt):
                for raw in parser.feed(text):
                    if (claim := book.claim(raw)) is not None:
                        emitted += 1
                        yield AnswerEvent(kind="claim", claim=claim)
        else:
            for raw in parser.feed(await generation_provider.generate(prompt)):
                if (claim := book.claim(raw)) is not None:
                    emitted += 1
                    yield AnswerEvent(kind="claim", claim=claim)
    except InvalidGroundedResponseError:
        raise
    except ProviderUnavailableError as error:
        raise AnswerServiceBusyError("answer models are busy") from error
    except Exception as error:
        raise AnsweringError("answer generation failed") from error
    if not parser.finished and emitted == 0:
        raise InvalidGroundedResponseError("model response was not valid claims JSON")
    if emitted == 0:
        raise UnsupportedQuestionError("the retrieved evidence does not answer the question")
    yield AnswerEvent(kind="citations", citations=book.citations())


async def answer_question(
    *,
    question: str,
    scope: Scope,
    session_factory: sessionmaker[Session],
    embedding_provider: EmbeddingProvider,
    generation_provider: GenerationProvider,
    settings: Settings,
    history: list[HistoryTurn] | None = None,
) -> GroundedAnswer:
    """Non-streaming convenience wrapper around ``answer_events``."""

    claims: list[Claim] = []
    citations: list[Citation] = []
    retrieval_query = question
    async for event in answer_events(
        question=question,
        scope=scope,
        history=history or [],
        session_factory=session_factory,
        embedding_provider=embedding_provider,
        generation_provider=generation_provider,
        settings=settings,
    ):
        if event.kind == "retrieved" and event.retrieval_query:
            retrieval_query = event.retrieval_query
        elif event.kind == "claim" and event.claim is not None:
            claims.append(event.claim)
        elif event.kind == "citations" and event.citations is not None:
            citations = event.citations
    return GroundedAnswer(claims=claims, citations=citations, retrieval_query=retrieval_query)


def list_courses(session_factory: sessionmaker[Session]) -> list[CourseSummary]:
    """Return every course with ingested documents, ordered by subject then course."""

    statement = (
        select(
            Document.subject,
            Document.course_id,
            func.count(Document.id).label("document_count"),
            func.coalesce(func.sum(Document.page_count), 0).label("page_count"),
        )
        .group_by(Document.subject, Document.course_id)
        .order_by(Document.subject, Document.course_id)
    )
    with session_factory() as session:
        rows = session.execute(statement).all()
    return [
        CourseSummary(
            subject=row.subject,
            course_id=row.course_id,
            document_count=int(row.document_count),
            page_count=int(row.page_count),
        )
        for row in rows
    ]


def list_documents(
    session_factory: sessionmaker[Session], course_id: str | None = None
) -> list[DocumentSummary]:
    """Return ingested documents (optionally of one course), ordered for a library view."""

    statement = select(Document).order_by(
        Document.subject, Document.course_id, Document.source_filename
    )
    if course_id is not None:
        statement = statement.where(Document.course_id == validate_course_id(course_id))
    with session_factory() as session:
        documents = session.scalars(statement).all()
    return [
        DocumentSummary(
            id=str(document.id),
            subject=document.subject,
            course_id=document.course_id,
            title=document.title or document.source_filename,
            source_filename=document.source_filename,
            page_count=document.page_count,
        )
        for document in documents
    ]
