"""Offline tests for scoped retrieval, quote-verified claims, streaming, and follow-ups."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.answering import (
    AnswerEvent,
    HistoryTurn,
    InvalidGroundedResponseError,
    NoEvidenceError,
    Scope,
    UnsupportedQuestionError,
    _ClaimStreamParser,
    answer_events,
    answer_question,
    list_courses,
)
from app.config import Settings

DOCUMENT = UUID("00000000-0000-0000-0000-0000000000d1")
CHUNK_A = UUID("00000000-0000-0000-0000-00000000000a")
CHUNK_B = UUID("00000000-0000-0000-0000-00000000000b")


def _row(
    chunk_id: UUID = CHUNK_A,
    *,
    text: str = "RSA repose sur la factorisation.\nÉtant donné n = pq, retrouver p et q.",
    distance: float = 0.1,
    page: int = 55,
    course_id: str = "Cryptographie",
) -> SimpleNamespace:
    first_line_end = text.find("\n") if "\n" in text else len(text)
    return SimpleNamespace(
        id=chunk_id,
        document_id=DOCUMENT,
        course_id=course_id,
        physical_page_number=page,
        section_title="RSA : un chiffrement asymétrique",
        text=text,
        locations=[
            {"s": 0, "e": first_line_end, "p": page, "b": [0.1, 0.2, 0.8, 0.25]},
            {"s": first_line_end + 1, "e": len(text), "p": page, "b": [0.1, 0.3, 0.8, 0.35]},
        ],
        subject="Général",
        title="Introduction à la cryptologie",
        source_filename="Cryptographie/slides.pdf",
        distance=distance,
    )


class _Sessions:
    """Records the compiled query and returns the given rows ordered by distance."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.sql = ""
        self.params: dict[str, Any] = {}

    def __call__(self) -> AbstractContextManager[Any]:
        sessions = self

        class _Session:
            def __enter__(self) -> _Session:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, statement: Any) -> Any:
                compiled = statement.compile(dialect=postgresql.dialect())
                sessions.sql = str(compiled)
                sessions.params = dict(compiled.params)
                rows = sorted(sessions.rows, key=lambda row: getattr(row, "distance", 0))
                return SimpleNamespace(all=lambda: rows)

        return _Session()


class _Embedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.25] * 1024


class _Generator:
    def __init__(self, response: str, rewritten: str | None = None) -> None:
        self.response = response
        self.rewritten = rewritten
        self.prompts: list[str] = []

    async def generate(self, prompt: str, *, json_output: bool = True) -> str:
        self.prompts.append(prompt)
        if not json_output:
            return self.rewritten or prompt.rsplit("\n", 1)[-1]
        return self.response


class _StreamingGenerator(_Generator):
    def __init__(self, pieces: list[str]) -> None:
        super().__init__("".join(pieces))
        self.pieces = pieces

    async def generate_stream(self, prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        for piece in self.pieces:
            yield piece


def _answer(**overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "question": "Comment fonctionne RSA ?",
        "scope": Scope(),
        "session_factory": cast(sessionmaker[Session], _Sessions([_row()])),
        "embedding_provider": _Embedder(),
        "generation_provider": _Generator('{"claims":[]}'),
        "settings": Settings(),
    }
    arguments.update(overrides)
    return asyncio.run(answer_question(**arguments))


def _claims(*claims: dict[str, Any]) -> str:
    return json.dumps({"claims": list(claims)}, ensure_ascii=False)


def test_claims_cite_labels_and_verified_quotes_become_highlights() -> None:
    generator = _Generator(
        _claims(
            {
                "text": "RSA repose sur la difficulté de factoriser n = pq.",
                "sources": [{"id": "S1", "quote": "étant donné n = pq, retrouver p et q"}],
            }
        )
    )

    answer = _answer(generation_provider=generator)

    assert [claim.model_dump() for claim in answer.claims] == [
        {"text": "RSA repose sur la difficulté de factoriser n = pq.", "citations": [1]}
    ]
    citation = answer.citations[0]
    assert (citation.number, citation.document_id, citation.page) == (1, str(DOCUMENT), 55)
    assert citation.quotes == ["Étant donné n = pq, retrouver p et q"]
    # Only the quoted line is highlighted, not the whole excerpt.
    assert [item.model_dump() for item in citation.highlights] == [
        {"page": 55, "boxes": [[0.1, 0.3, 0.8, 0.35]], "lines": []}
    ]
    assert '"id": "S1"' in generator.prompts[0]
    assert "langue de la question" in generator.prompts[0]


def test_unverifiable_quotes_highlight_the_whole_excerpt() -> None:
    answer = _answer(
        generation_provider=_Generator(
            _claims(
                {
                    "text": "Une affirmation.",
                    "sources": [{"id": "S1", "quote": "texte absent de l'extrait"}],
                }
            )
        )
    )

    assert answer.citations[0].quotes == []
    assert len(answer.citations[0].highlights[0].boxes) == 2


def test_unknown_sources_are_dropped_and_unsupported_claims_hidden() -> None:
    generator = _Generator(
        _claims(
            {"text": "Soutenue.", "sources": [{"id": "S1", "quote": "RSA repose"}, {"id": "S9"}]},
            {"text": "Inventée.", "sources": [{"id": "S7", "quote": "rien"}]},
        )
    )

    answer = _answer(generation_provider=generator)

    assert [claim.text for claim in answer.claims] == ["Soutenue."]
    assert [citation.number for citation in answer.citations] == [1]


def test_citations_are_numbered_in_order_of_first_use() -> None:
    sessions = _Sessions(
        [_row(CHUNK_A, distance=0.1), _row(CHUNK_B, text="AES chiffre par blocs.", distance=0.2)]
    )
    generator = _Generator(
        _claims(
            {"text": "Premier.", "sources": [{"id": "S2", "quote": "AES chiffre par blocs"}]},
            {"text": "Second.", "sources": [{"id": "S1", "quote": "RSA repose"}, {"id": "S2"}]},
        )
    )

    answer = _answer(session_factory=sessions, generation_provider=generator)

    assert [claim.citations for claim in answer.claims] == [[1], [2, 1]]
    assert [citation.chunk_id for citation in answer.citations] == [str(CHUNK_B), str(CHUNK_A)]


def test_empty_claims_or_no_evidence_abstain() -> None:
    with pytest.raises(UnsupportedQuestionError):
        _answer(generation_provider=_Generator('{"claims":[]}'))

    generator = _Generator(_claims({"text": "x", "sources": [{"id": "S1"}]}))
    with pytest.raises(NoEvidenceError):
        _answer(
            session_factory=cast(sessionmaker[Session], _Sessions([])),
            generation_provider=generator,
        )
    assert generator.prompts == []  # nothing is generated without evidence

    low = _Sessions([_row(distance=0.95)])
    with pytest.raises(NoEvidenceError):
        _answer(session_factory=cast(sessionmaker[Session], low), generation_provider=generator)


def test_malformed_model_output_is_rejected() -> None:
    with pytest.raises(InvalidGroundedResponseError):
        _answer(generation_provider=_Generator("not json"))


@pytest.mark.parametrize(
    ("scope", "fragment"),
    [
        (Scope(course_id="Cryptographie"), "document_chunks.course_id = "),
        (Scope(subject="Informatique"), "documents.subject = "),
        (Scope(document_id=DOCUMENT), "document_chunks.document_id = "),
    ],
)
def test_scope_filters_in_sql_before_ranking(scope: Scope, fragment: str) -> None:
    sessions = _Sessions([_row()])
    _answer(
        scope=scope,
        session_factory=cast(sessionmaker[Session], sessions),
        generation_provider=_Generator(_claims({"text": "x", "sources": [{"id": "S1"}]})),
    )

    assert fragment in sessions.sql
    assert sessions.sql.index("WHERE") < sessions.sql.index("ORDER BY")


def test_unscoped_search_has_no_filter() -> None:
    sessions = _Sessions([_row()])
    _answer(
        session_factory=cast(sessionmaker[Session], sessions),
        generation_provider=_Generator(_claims({"text": "x", "sources": [{"id": "S1"}]})),
    )
    assert "WHERE" not in sessions.sql


def test_follow_up_questions_are_rewritten_before_searching() -> None:
    embedder = _Embedder()
    generator = _Generator(
        _claims({"text": "x", "sources": [{"id": "S1"}]}), rewritten="Comment fonctionne AES ?"
    )
    history = [
        HistoryTurn(role="user", content="Comment fonctionne RSA ?"),
        HistoryTurn(role="assistant", content="RSA repose sur la factorisation."),
    ]

    answer = _answer(
        question="Et pour AES ?",
        history=history,
        embedding_provider=embedder,
        generation_provider=generator,
    )

    assert embedder.queries == ["Comment fonctionne AES ?"]
    assert answer.retrieval_query == "Comment fonctionne AES ?"
    assert "Et pour AES ?" in generator.prompts[0]  # the rewrite prompt
    assert "Conversation précédente" in generator.prompts[1]  # the answer sees the history
    assert "Question :\nEt pour AES ?" in generator.prompts[1]


def test_first_question_is_searched_as_asked() -> None:
    embedder = _Embedder()
    generator = _Generator(_claims({"text": "x", "sources": [{"id": "S1"}]}))
    _answer(embedding_provider=embedder, generation_provider=generator)
    assert embedder.queries == ["Comment fonctionne RSA ?"]
    assert len(generator.prompts) == 1


def test_streamed_claims_are_emitted_as_soon_as_each_is_complete() -> None:
    response = _claims(
        {"text": "Premier.", "sources": [{"id": "S1", "quote": "RSA repose"}]},
        {"text": "Second, avec une accolade } dans le texte.", "sources": [{"id": "S1"}]},
    )
    pieces = [response[index : index + 7] for index in range(0, len(response), 7)]
    generator = _StreamingGenerator(pieces)

    async def collect() -> list[AnswerEvent]:
        return [
            event
            async for event in answer_events(
                question="Comment fonctionne RSA ?",
                scope=Scope(),
                history=[],
                session_factory=cast(sessionmaker[Session], _Sessions([_row()])),
                embedding_provider=_Embedder(),
                generation_provider=generator,
                settings=Settings(),
            )
        ]

    events = asyncio.run(collect())

    assert [event.kind for event in events] == ["retrieved", "claim", "claim", "citations"]
    assert events[2].claim is not None and "accolade }" in events[2].claim.text


def test_claim_parser_handles_arbitrary_chunk_boundaries() -> None:
    parser = _ClaimStreamParser()
    text = '{"claims": [ {"text": "a \\"b\\"", "sources": []} , {"text": "c", "sources": []} ]}'
    found: list[dict[str, Any]] = []
    for character in text:
        found.extend(parser.feed(character))
    assert [claim["text"] for claim in found] == ['a "b"', "c"]
    assert parser.finished


def test_list_courses_groups_documents_by_subject_and_course() -> None:
    rows = [SimpleNamespace(subject="Général", course_id="stat", document_count=6, page_count=116)]
    sessions = _Sessions(rows)

    courses = list_courses(cast(sessionmaker[Session], sessions))

    assert "GROUP BY documents.subject, documents.course_id" in sessions.sql
    assert courses[0].model_dump() == {
        "subject": "Général",
        "course_id": "stat",
        "document_count": 6,
        "page_count": 116,
    }
