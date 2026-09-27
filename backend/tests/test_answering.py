"""Offline tests for course-scoped retrieval and citation validation."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.answering import (
    InvalidGroundedResponseError,
    NoEvidenceError,
    UnsupportedQuestionError,
    answer_question,
    list_courses,
)
from app.config import Settings


class _FakeResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self.rows


class _FakeSession:
    def __init__(self, factory: _FakeSessionFactory) -> None:
        self.factory = factory

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: Any) -> _FakeResult:
        compiled = statement.compile(dialect=postgresql.dialect())
        self.factory.statement = str(compiled)
        self.factory.parameters = compiled.params
        requested_course = next(
            value for value in compiled.params.values() if value == "course-a"
        )
        filtered_rows = [row for row in self.factory.rows if row.course_id == requested_course]
        return _FakeResult(
            sorted(filtered_rows, key=lambda row: row.distance)[: self.factory.limit]
        )


class _FakeSessionFactory:
    def __init__(self, rows: list[SimpleNamespace], limit: int = 8) -> None:
        self.rows = rows
        self.limit = limit
        self.statement = ""
        self.parameters: dict[str, Any] = {}

    def __call__(self) -> AbstractContextManager[_FakeSession]:
        return cast(AbstractContextManager[_FakeSession], _FakeSession(self))


class _FixedEmbedder:
    async def embed(self, _text: str) -> list[float]:
        return [0.25] * 768


class _FixedGenerator:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompt = ""

    async def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.response


def _evidence_row(
    *,
    chunk_id: str = "00000000-0000-0000-0000-000000000002",
    text: str = "Water boils at 100 degrees Celsius at sea level.",
    filename: str = "Cours1.pdf",
    page: int = 2,
    distance: float = 0.1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(chunk_id),
        subject="Sciences",
        course_id="course-a",
        title="Cours 1",
        section_title="Chapitre 1 > Ébullition",
        physical_page_number=page,
        text=text,
        source_filename=filename,
        distance=distance,
    )


def test_retrieval_filters_course_in_sql_before_vector_ranking() -> None:
    citation_id = "00000000-0000-0000-0000-000000000002"
    generator = _FixedGenerator(
        '{"claims":[{"text":"Water boils at 100 degrees Celsius at sea level.",'
        '"citation_ids":["' + citation_id + '"]}]}'
    )
    session_factory = _FakeSessionFactory([_evidence_row()])
    wrong_course = _evidence_row(
        chunk_id="00000000-0000-0000-0000-000000000003",
        text="Wrong course fact.",
        filename="OtherCourse.pdf",
        distance=0.01,
    )
    wrong_course.course_id = "course-b"
    session_factory.rows.append(wrong_course)

    answer = asyncio.run(
        answer_question(
            question="What is the boiling point of water?",
            course_id="course-a",
            session_factory=cast(sessionmaker[Session], session_factory),
            embedding_provider=_FixedEmbedder(),
            generation_provider=generator,
            settings=Settings(),
        )
    )

    where_position = session_factory.statement.index("WHERE")
    order_position = session_factory.statement.index("ORDER BY")
    assert where_position < order_position
    assert "document_chunks.course_id" in session_factory.statement
    assert "ix_chunks_embedding_hnsw" not in session_factory.statement
    assert "course-a" in session_factory.parameters.values()
    assert answer.claims[0].text.startswith("Water boils")
    assert answer.citations[0].source_filename == "Cours1.pdf"
    assert answer.citations[0].physical_page_number == 2
    assert answer.citations[0].excerpt == "Water boils at 100 degrees Celsius at sea level."
    assert all(citation.source_filename != "OtherCourse.pdf" for citation in answer.citations)
    assert "Water boils at 100 degrees Celsius" in generator.prompt
    assert "What is the boiling point of water?" in generator.prompt


def test_unresolved_citation_is_rejected_before_returning_an_answer() -> None:
    generator = _FixedGenerator(
        '{"claims":[{"text":"An unsupported claim.","citation_ids":["not-retrieved"]}]}'
    )
    with pytest.raises(InvalidGroundedResponseError, match="not retrieved"):
        asyncio.run(
            answer_question(
                question="What happened?",
                course_id="course-a",
                session_factory=cast(
                    sessionmaker[Session], _FakeSessionFactory([_evidence_row()])
                ),
                embedding_provider=_FixedEmbedder(),
                generation_provider=generator,
                settings=Settings(),
            )
        )


def test_no_relevant_evidence_does_not_call_generation() -> None:
    generator = _FixedGenerator('{"claims":[]}')
    with pytest.raises(NoEvidenceError):
        asyncio.run(
            answer_question(
                question="What happened?",
                course_id="course-a",
                session_factory=cast(sessionmaker[Session], _FakeSessionFactory([])),
                embedding_provider=_FixedEmbedder(),
                generation_provider=generator,
                settings=Settings(),
            )
        )
    assert generator.prompt == ""


def test_unrelated_factual_context_is_not_added_to_generation_prompt() -> None:
    citation_id = "00000000-0000-0000-0000-000000000002"
    generator = _FixedGenerator(
        '{"claims":[{"text":"A supported fact.",'
        '"citation_ids":["' + citation_id + '"]}]}'
    )
    asyncio.run(
        answer_question(
            question="Question text",
            course_id="course-a",
            session_factory=cast(
                sessionmaker[Session],
                _FakeSessionFactory([_evidence_row(text="A supported fact.")]),
            ),
            embedding_provider=_FixedEmbedder(),
            generation_provider=generator,
            settings=Settings(),
        )
    )

    assert "Question text" in generator.prompt
    assert "A supported fact." in generator.prompt
    assert "different course" not in generator.prompt
    assert "general knowledge" not in generator.prompt


class _AllCoursesSessionFactory:
    """Records the statement and returns rows unfiltered, like a query with no course filter."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.statement = ""

    def __call__(self) -> Any:
        factory = self

        class _Session:
            def __enter__(self) -> _Session:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, statement: Any) -> _FakeResult:
                factory.statement = str(statement.compile(dialect=postgresql.dialect()))
                return _FakeResult(factory.rows)

        return _Session()


def test_missing_course_searches_all_courses_and_cites_each_course() -> None:
    first = _evidence_row(text="Course A fact.")
    second = _evidence_row(
        chunk_id="00000000-0000-0000-0000-000000000003", text="Course B fact.", distance=0.2
    )
    second.course_id = "course-b"
    generator = _FixedGenerator(
        '{"claims":[{"text":"Both facts.","citation_ids":["'
        + str(first.id) + '","' + str(second.id) + '"]}]}'
    )
    session_factory = _AllCoursesSessionFactory([first, second])

    answer = asyncio.run(
        answer_question(
            question="Which facts?",
            course_id=None,
            session_factory=cast(sessionmaker[Session], session_factory),
            embedding_provider=_FixedEmbedder(),
            generation_provider=generator,
            settings=Settings(),
        )
    )

    assert "WHERE" not in session_factory.statement
    assert [citation.course_id for citation in answer.citations] == ["course-a", "course-b"]


def test_empty_claims_mean_the_evidence_does_not_answer() -> None:
    with pytest.raises(UnsupportedQuestionError):
        asyncio.run(
            answer_question(
                question="Unrelated question?",
                course_id="course-a",
                session_factory=cast(
                    sessionmaker[Session], _FakeSessionFactory([_evidence_row()])
                ),
                embedding_provider=_FixedEmbedder(),
                generation_provider=_FixedGenerator('{"claims":[]}'),
                settings=Settings(),
            )
        )


def test_prompt_asks_for_question_language_and_explicit_abstention() -> None:
    citation_id = "00000000-0000-0000-0000-000000000002"
    generator = _FixedGenerator(
        '{"claims":[{"text":"Un fait.","citation_ids":["' + citation_id + '"]}]}'
    )
    asyncio.run(
        answer_question(
            question="Quelle est la réponse ?",
            course_id="course-a",
            session_factory=cast(
                sessionmaker[Session], _FakeSessionFactory([_evidence_row()])
            ),
            embedding_provider=_FixedEmbedder(),
            generation_provider=generator,
            settings=Settings(),
        )
    )

    assert "same language as the question" in generator.prompt
    assert '{"claims":[]}' in generator.prompt


def test_list_courses_groups_documents_by_course() -> None:
    rows = [
        SimpleNamespace(
            subject="Informatique", course_id="algo-101", document_count=2, page_count=40
        ),
        SimpleNamespace(subject="Maths", course_id="math-200", document_count=1, page_count=12),
    ]
    session_factory = _AllCoursesSessionFactory(rows)

    courses = list_courses(cast(sessionmaker[Session], session_factory))

    assert "GROUP BY documents.subject, documents.course_id" in session_factory.statement
    assert [course.model_dump() for course in courses] == [
        {"subject": "Informatique", "course_id": "algo-101", "document_count": 2,
         "page_count": 40},
        {"subject": "Maths", "course_id": "math-200", "document_count": 1, "page_count": 12},
    ]


def test_subject_scope_filters_in_sql_and_evidence_carries_its_place_in_the_course() -> None:
    citation_id = "00000000-0000-0000-0000-000000000002"
    generator = _FixedGenerator(
        '{"claims":[{"text":"Water boils at 100 C.","citation_ids":["' + citation_id + '"]}]}'
    )
    session_factory = _AllCoursesSessionFactory([_evidence_row()])

    answer = asyncio.run(
        answer_question(
            question="Boiling point?",
            course_id=None,
            subject="Sciences",
            session_factory=cast(sessionmaker[Session], session_factory),
            embedding_provider=_FixedEmbedder(),
            generation_provider=generator,
            settings=Settings(),
        )
    )

    assert "documents.subject = " in session_factory.statement
    assert '"subject": "Sciences"' in generator.prompt
    assert '"document": "Cours 1"' in generator.prompt
    assert '"section": "Chapitre 1 > Ébullition"' in generator.prompt
    citation = answer.citations[0]
    assert (citation.subject, citation.document_title, citation.section_title) == (
        "Sciences", "Cours 1", "Chapitre 1 > Ébullition"
    )
