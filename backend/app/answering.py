"""Course-filtered retrieval and evidence-validated answer generation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingestion import validate_course_id, validate_subject
from app.models import Document, DocumentChunk
from app.providers.protocols import EmbeddingProvider, GenerationProvider


class AnsweringError(RuntimeError):
    """A question could not be answered safely from the selected course."""


class NoEvidenceError(AnsweringError):
    """No sufficiently relevant evidence exists in the requested course."""


class UnsupportedQuestionError(AnsweringError):
    """Evidence was retrieved, but the model found that it does not answer the question."""


class InvalidGroundedResponseError(AnsweringError):
    """The model response was malformed or cited evidence it was not given."""


class _GeneratedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    text: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)


class _GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    # An empty list is the model's explicit signal that the evidence does not answer.
    claims: list[_GeneratedClaim]


class AnswerClaim(BaseModel):
    """One supported answer claim and the evidence IDs that support it."""

    text: str
    citation_ids: list[str]


class AnswerCitation(BaseModel):
    """A cited excerpt with physical PDF provenance."""

    citation_id: str
    subject: str
    course_id: str
    document_title: str | None
    section_title: str | None
    source_filename: str
    physical_page_number: int
    excerpt: str


class CourseSummary(BaseModel):
    """A course that has ingested material available for questions."""

    subject: str
    course_id: str
    document_count: int
    page_count: int


class GroundedAnswer(BaseModel):
    """Validated claims and only the evidence cited by those claims."""

    claims: list[AnswerClaim]
    citations: list[AnswerCitation]


@dataclass(frozen=True)
class _RetrievedEvidence:
    citation_id: str
    subject: str
    course_id: str
    document_title: str | None
    section_title: str | None
    source_filename: str
    physical_page_number: int
    excerpt: str
    score: float


def _retrieve(
    *,
    session_factory: sessionmaker[Session],
    subject: str | None,
    course_id: str | None,
    query_embedding: list[float],
    limit: int,
    minimum_score: float,
) -> list[_RetrievedEvidence]:
    """Filter to the requested subject/course (if any) in SQL before ranking and limiting."""

    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    statement = (
        select(
            DocumentChunk.id,
            DocumentChunk.course_id,
            DocumentChunk.physical_page_number,
            DocumentChunk.section_title,
            DocumentChunk.text,
            Document.subject,
            Document.title,
            Document.source_filename,
            distance.label("distance"),
        )
        .join(
            Document,
            (Document.id == DocumentChunk.document_id)
            & (Document.course_id == DocumentChunk.course_id),
        )
    )
    if course_id is not None:
        statement = statement.where(
            DocumentChunk.course_id == course_id, Document.course_id == course_id
        )
    if subject is not None:
        statement = statement.where(Document.subject == subject)
    statement = statement.order_by(distance).limit(limit)
    with session_factory() as session:
        rows = session.execute(statement).all()

    evidence: list[_RetrievedEvidence] = []
    for row in rows:
        score = 1.0 - float(row.distance)
        if not math.isfinite(score) or score < minimum_score:
            continue
        evidence.append(
            _RetrievedEvidence(
                citation_id=str(row.id),
                subject=row.subject,
                course_id=row.course_id,
                document_title=row.title,
                section_title=row.section_title,
                source_filename=row.source_filename,
                physical_page_number=row.physical_page_number,
                excerpt=row.text,
                score=score,
            )
        )
    return evidence


def _generation_prompt(question: str, evidence: list[_RetrievedEvidence]) -> str:
    """Build a prompt whose only course facts are the retrieved excerpts."""

    excerpts = json.dumps(
        [
            {
                "citation_id": item.citation_id,
                "subject": item.subject,
                "course": item.course_id,
                "document": item.document_title or item.source_filename,
                "section": item.section_title,
                "page": item.physical_page_number,
                "excerpt": item.excerpt,
            }
            for item in evidence
        ],
        ensure_ascii=False,
    )
    return (
        "You are a university course assistant. "
        "The evidence is excerpts of lecture slides; each names its subject, course, "
        "document, section, and page. Slides are terse: connect related excerpts into "
        "complete sentences, but do not add facts they do not state. "
        "Answer the question using only the factual information in the evidence below. "
        "Return valid JSON with exactly this shape: "
        '{"claims":[{"text":"supported claim","citation_ids":["evidence id"]}]}. '
        "Each substantive claim must cite one or more supplied evidence IDs. "
        "Do not invent facts, citations, or evidence. "
        'If the evidence does not answer the question, return {"claims":[]}. '
        "Write every claim in the same language as the question.\n\n"
        f"Question:\n{question}\n\nEvidence:\n{excerpts}"
    )


async def answer_question(
    *,
    question: str,
    course_id: str | None,
    session_factory: sessionmaker[Session],
    embedding_provider: EmbeddingProvider,
    generation_provider: GenerationProvider,
    settings: Settings,
    subject: str | None = None,
) -> GroundedAnswer:
    """Retrieve course evidence, generate claims, and validate every citation.

    ``course_id`` limits the search to one course and ``subject`` to one subject; when
    both are ``None`` every ingested course is searched.
    """

    normalized_course_id = None if course_id is None else validate_course_id(course_id)
    normalized_subject = None if subject is None else validate_subject(subject)
    normalized_question = question.strip()
    if not normalized_question:
        raise AnsweringError("question must not be empty")

    try:
        query_embedding = await embedding_provider.embed(normalized_question)
    except Exception as error:
        raise AnsweringError("question embedding failed") from error
    if len(query_embedding) != settings.embedding_dimensions:
        raise AnsweringError(
            f"question embedding returned {len(query_embedding)} dimensions; "
            f"expected {settings.embedding_dimensions}"
        )
    if not all(math.isfinite(value) for value in query_embedding):
        raise AnsweringError("question embedding contains a non-finite value")

    evidence = _retrieve(
        session_factory=session_factory,
        subject=normalized_subject,
        course_id=normalized_course_id,
        query_embedding=query_embedding,
        limit=settings.retrieval_limit,
        minimum_score=settings.minimum_score,
    )
    if not evidence:
        raise NoEvidenceError("no sufficiently relevant evidence was found for this course")

    try:
        generated_text = await generation_provider.generate(
            _generation_prompt(normalized_question, evidence)
        )
        generated = _GeneratedAnswer.model_validate_json(generated_text)
    except (ValidationError, ValueError) as error:
        raise InvalidGroundedResponseError(
            "model response was not valid cited-claims JSON"
        ) from error
    except Exception as error:
        raise AnsweringError("answer generation failed") from error

    if not generated.claims:
        raise UnsupportedQuestionError("the retrieved evidence does not answer the question")

    evidence_by_id = {item.citation_id: item for item in evidence}
    cited_ids: list[str] = []
    for claim in generated.claims:
        unknown_ids = set(claim.citation_ids) - evidence_by_id.keys()
        if unknown_ids:
            raise InvalidGroundedResponseError(
                "model response cited evidence that was not retrieved for this course"
            )
        for citation_id in claim.citation_ids:
            if citation_id not in cited_ids:
                cited_ids.append(citation_id)

    citations = [
        AnswerCitation(
            citation_id=item.citation_id,
            subject=item.subject,
            course_id=item.course_id,
            document_title=item.document_title,
            section_title=item.section_title,
            source_filename=item.source_filename,
            physical_page_number=item.physical_page_number,
            excerpt=item.excerpt,
        )
        for citation_id in cited_ids
        if (item := evidence_by_id[citation_id])
    ]
    return GroundedAnswer(
        claims=[
            AnswerClaim(text=claim.text, citation_ids=claim.citation_ids)
            for claim in generated.claims
        ],
        citations=citations,
    )


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
