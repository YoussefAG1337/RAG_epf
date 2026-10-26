"""Integration tests against a real PostgreSQL + pgvector (skipped when unavailable).

A throwaway ``<name>_test`` database is created, migrated with Alembic, and dropped, so
the developer's data is never touched. Point TEST_DATABASE_HOST/TEST_DATABASE_PORT at a
running server (for example the Compose database) to run them.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.answering import Scope, _retrieve, answer_question, list_courses, list_documents
from app.config import Settings
from app.conversations import (
    append_messages,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    start_or_continue,
)
from app.ingestion import ingest_document
from app.models import Document, DocumentChunk
from app.providers.deterministic import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
)

BACKEND = Path(__file__).resolve().parents[1]


def _server_url(database: str) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DATABASE_USER", "course_rag"),
        password=os.environ.get("DATABASE_PASSWORD", "local-development-only"),
        host=os.environ.get("TEST_DATABASE_HOST", "localhost"),
        port=int(os.environ.get("TEST_DATABASE_PORT", "5433")),
        database=database,
    )


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE IF EXISTS course_rag_test"))
            connection.execute(text("CREATE DATABASE course_rag_test"))
    except OperationalError:
        admin.dispose()
        pytest.skip("no PostgreSQL server reachable for integration tests")
    url = _server_url("course_rag_test")
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE IF EXISTS course_rag_test WITH (FORCE)"))
        admin.dispose()


def _settings(tmp: Path) -> Settings:
    return Settings(pdf_source_dir=tmp, evidence_minimum_score=0.05)


def test_ingested_markdown_is_listed_retrieved_and_cited_with_line_numbers(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    (tmp_path / "Statistiques").mkdir()
    (tmp_path / "Statistiques" / "fiche.md").write_text(
        "# Fiche de synthèse\n\n## Mesures de tendance centrale\n\n"
        "- **Moyenne** : somme des valeurs divisée par leur nombre.\n"
        "- **Médiane** : valeur centrale des données triées.\n\n"
        "## Probabilités\n\nLe théorème de Bayes relie P(A|B) et P(B|A).\n",
        encoding="utf-8",
    )
    embedder = DeterministicEmbeddingProvider(dimensions=Settings().embedding_dimensions)
    result = asyncio.run(
        ingest_document(
            source_directory=tmp_path,
            selected_file="Statistiques/fiche.md",
            subject="Général",
            course_id="Statistiques",
            session_factory=sessions,
            embedding_provider=embedder,
        )
    )
    assert result.chunk_count == 2

    assert [course.course_id for course in list_courses(sessions)] == ["Statistiques"]
    (document,) = list_documents(sessions, "Statistiques")
    assert document.title == "Fiche de synthèse"

    answer = asyncio.run(
        answer_question(
            question="Quelle est la définition de la médiane ?",
            scope=Scope(document_id=UUID(document.id)),
            session_factory=sessions,
            embedding_provider=embedder,
            generation_provider=DeterministicGenerationProvider(),
            settings=_settings(tmp_path),
        )
    )
    citation = answer.citations[0]
    assert citation.section_title == "Mesures de tendance centrale"
    # The deterministic answer quotes the chunk's first line: the section heading (line 3).
    assert citation.highlights[0].lines == [3]

    other_course = Scope(course_id="Cryptographie")
    with pytest.raises(Exception, match="no sufficiently relevant evidence"):
        asyncio.run(
            answer_question(
                question="Quelle est la définition de la médiane ?",
                scope=other_course,
                session_factory=sessions,
                embedding_provider=embedder,
                generation_provider=DeterministicGenerationProvider(),
                settings=_settings(tmp_path),
            )
        )


def test_narrow_scope_returns_every_in_scope_chunk_despite_the_hnsw_index(
    sessions: sessionmaker[Session],
) -> None:
    # pgvector's HNSW index filters *after* collecting its candidates, so a scope that is a
    # small share of the corpus could come back short if the planner chose the index. A
    # scoped search must return what an exact search returns, whichever plan is used.
    embedder = DeterministicEmbeddingProvider(dimensions=Settings().embedding_dimensions)
    small_document = uuid5(NAMESPACE_URL, "regression/small")
    with sessions.begin() as session:
        for course, document_id, count in (
            ("Grand", uuid5(NAMESPACE_URL, "regression/large"), 300),
            ("Petit", small_document, 5),
        ):
            session.add(
                Document(
                    id=document_id,
                    subject="Général",
                    course_id=course,
                    source_filename=f"{course}/cours.md",
                    checksum="0" * 64,
                    page_count=1,
                    embedding_provider="deterministic",
                )
            )
            session.flush()
            for position in range(count):
                content = f"{course} chapitre {position} chiffrement clé publique"
                session.add(
                    DocumentChunk(
                        document_id=document_id,
                        course_id=course,
                        physical_page_number=1,
                        chunk_position=position,
                        text=content,
                        embedding=asyncio.run(embedder.embed(content)),
                    )
                )
    query = asyncio.run(embedder.embed("Grand chapitre 7 chiffrement"))

    for scope in (Scope(course_id="Petit"), Scope(document_id=small_document)):
        found = _retrieve(
            session_factory=sessions,
            scope=scope,
            query_embedding=query,
            limit=8,
            minimum_score=-1.0,
        )
        assert len(found) == 5
        assert {item.course_id for item in found} == {"Petit"}


def test_refusals_and_errors_are_not_given_to_the_model_as_history(
    sessions: sessionmaker[Session],
) -> None:
    conversation_id, _ = start_or_continue(sessions, None, "Quel temps fait-il ?", {})
    append_messages(
        sessions,
        conversation_id,
        [
            ("user", "Quel temps fait-il ?", {}),
            ("assistant", "Je n'ai pas trouvé cette information…", {"status": "abstained"}),
            ("user", "Comment fonctionne RSA ?", {}),
            ("assistant", "RSA…", {"status": "answered"}),
        ],
    )

    _, history = start_or_continue(sessions, conversation_id, "Et AES ?", {})

    assert [(turn.role, turn.content) for turn in history] == [
        ("user", "Quel temps fait-il ?"),
        ("user", "Comment fonctionne RSA ?"),
        ("assistant", "RSA…"),
    ]


def test_conversations_keep_ordered_messages_scope_and_can_be_renamed_and_deleted(
    sessions: sessionmaker[Session],
) -> None:
    conversation_id, history = start_or_continue(
        sessions, None, "Comment fonctionne RSA ?", {"course_id": "Cryptographie"}
    )
    assert history == []
    payload: dict[str, Any] = {
        "status": "answered",
        "claims": [{"text": "RSA…", "citations": [1]}],
        "citations": [],
    }
    append_messages(
        sessions,
        conversation_id,
        [("user", "Comment fonctionne RSA ?", {}), ("assistant", "RSA…", payload)],
    )

    same_id, history = start_or_continue(sessions, conversation_id, "Et AES ?", {"subject": "Info"})
    assert same_id == conversation_id
    assert [(turn.role, turn.content) for turn in history] == [
        ("user", "Comment fonctionne RSA ?"),
        ("assistant", "RSA…"),
    ]
    append_messages(
        sessions,
        conversation_id,
        [("user", "Et AES ?", {}), ("assistant", "AES…", {"status": "answered"})],
    )

    detail = get_conversation(sessions, conversation_id)
    assert detail is not None
    assert detail.title == "Comment fonctionne RSA ?"
    assert detail.scope == {"subject": "Info"}
    assert [message.content for message in detail.messages] == [
        "Comment fonctionne RSA ?",
        "RSA…",
        "Et AES ?",
        "AES…",
    ]
    assert detail.messages[1].payload == payload

    assert rename_conversation(sessions, conversation_id, "Cryptographie : RSA et AES") is not None
    assert list_conversations(sessions)[0].title == "Cryptographie : RSA et AES"

    unknown, _ = start_or_continue(sessions, UUID(int=12345), "Nouvelle question", {})
    assert unknown != UUID(int=12345)  # an unknown id starts a new conversation

    assert delete_conversation(sessions, conversation_id)
    assert get_conversation(sessions, conversation_id) is None
    assert not delete_conversation(sessions, conversation_id)
