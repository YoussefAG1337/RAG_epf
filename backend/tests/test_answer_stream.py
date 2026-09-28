"""Offline tests for the answer stream (v2), document endpoints, and conversation API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app import main
from app.answering import (
    AnswerEvent,
    AnswerServiceBusyError,
    Citation,
    Claim,
    Highlight,
    HistoryTurn,
    NoEvidenceError,
    Scope,
)
from app.config import Settings
from app.documents import DocumentInfo, DocumentKind, DocumentMissingError

CONVERSATION = UUID("00000000-0000-0000-0000-0000000000c1")
DOCUMENT = UUID("00000000-0000-0000-0000-0000000000d1")


def _citation() -> Citation:
    return Citation(
        number=1,
        chunk_id="chunk",
        document_id=str(DOCUMENT),
        subject="Général",
        course_id="Cryptographie",
        document_title="Introduction à la cryptologie",
        section_title="RSA",
        source_filename="Cryptographie/slides.pdf",
        page=52,
        excerpt="Bob choisit deux grands nombres premiers p et q.",
        quotes=["deux grands nombres premiers"],
        highlights=[Highlight(page=52, boxes=[[0.1, 0.2, 0.6, 0.25]], lines=[])],
    )


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace conversation storage with in-memory recording."""

    record: dict[str, Any] = {"started": [], "appended": []}

    def start(
        _factory: object, conversation_id: UUID | None, question: str, scope: dict[str, str]
    ) -> Any:
        record["started"].append((conversation_id, question, scope))
        history = [HistoryTurn("user", "Question précédente")] if conversation_id else []
        return CONVERSATION, history

    def append(_factory: object, conversation_id: UUID, messages: list[Any]) -> list[UUID]:
        record["appended"].append((conversation_id, messages))
        return [UUID(int=1), UUID(int=2)]

    monkeypatch.setattr(main, "start_or_continue", start)
    monkeypatch.setattr(main, "append_messages", append)
    return record


def _client(**kwargs: Any) -> TestClient:
    return TestClient(main.create_app(schema_guard=None, session_factory=object(), **kwargs))  # type: ignore[arg-type]


def _stream(client: TestClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    response = client.post("/api/v1/answers/stream", json=body)
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_answer_streams_claims_then_citations_and_saves_the_exchange(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, Any]
) -> None:
    captured: dict[str, Any] = {}

    async def fake_events(**kwargs: Any) -> AsyncIterator[AnswerEvent]:
        captured.update(kwargs)
        yield AnswerEvent(kind="retrieved", retrieval_query="Comment RSA génère-t-il ses clés ?")
        yield AnswerEvent(
            kind="claim", claim=Claim(text="On choisit p et q premiers.", citations=[1])
        )
        yield AnswerEvent(kind="claim", claim=Claim(text="On calcule n = pq.", citations=[1]))
        yield AnswerEvent(kind="citations", citations=[_citation()])

    monkeypatch.setattr(main, "answer_events", fake_events)
    events = _stream(
        _client(),
        {
            "question": "Et les clés ?",
            "conversation_id": str(CONVERSATION),
            "scope": {"course_id": "Cryptographie"},
        },
    )

    assert [event["type"] for event in events] == [
        "conversation",
        "status",
        "status",
        "claim",
        "claim",
        "citations",
        "completed",
    ]
    assert all(event["version"] == 2 for event in events)
    assert events[0]["conversation_id"] == str(CONVERSATION)
    assert events[2] == {
        "version": 2,
        "type": "status",
        "stage": "writing",
        "retrieval_query": "Comment RSA génère-t-il ses clés ?",
    }
    assert events[3] == {
        "version": 2,
        "type": "claim",
        "index": 0,
        "text": "On choisit p et q premiers.",
        "citations": [1],
    }
    assert events[5]["citations"][0]["highlights"] == [
        {"page": 52, "boxes": [[0.1, 0.2, 0.6, 0.25]], "lines": []}
    ]
    assert events[6]["message_id"] == str(UUID(int=2))
    assert captured["scope"] == Scope(course_id="Cryptographie")
    assert captured["history"][0].content == "Question précédente"

    ((conversation_id, messages),) = recorded["appended"]
    assert conversation_id == CONVERSATION
    assert messages[0] == ("user", "Et les clés ?", {"scope": {"course_id": "Cryptographie"}})
    role, content, payload = messages[1]
    assert (role, content, payload["status"]) == (
        "assistant",
        "On choisit p et q premiers. On calcule n = pq.",
        "answered",
    )
    assert payload["citations"][0]["number"] == 1


def test_overloaded_answer_models_get_a_specific_retry_message(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, Any]
) -> None:
    async def busy(**_kwargs: Any) -> AsyncIterator[AnswerEvent]:
        raise AnswerServiceBusyError("busy")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "answer_events", busy)
    events = _stream(_client(), {"question": "Comment fonctionne RSA ?"})

    assert events[-1]["type"] == "error"
    assert "saturé" in events[-1]["message"]
    assert recorded["appended"][0][1][1][1] == events[-1]["message"]


def test_missing_evidence_streams_and_saves_an_abstention(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, Any]
) -> None:
    async def abstain(**_kwargs: Any) -> AsyncIterator[AnswerEvent]:
        raise NoEvidenceError("nothing")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "answer_events", abstain)
    events = _stream(_client(), {"question": "Qui a gagné la coupe du monde ?"})

    assert [event["type"] for event in events] == ["conversation", "status", "abstention"]
    assert "pas trouvé" in events[-1]["message"]
    payload = recorded["appended"][0][1][1][2]
    assert payload["status"] == "abstained" and payload["claims"] == []


def test_failures_end_with_one_safe_error(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, Any]
) -> None:
    async def fail(**_kwargs: Any) -> AsyncIterator[AnswerEvent]:
        raise RuntimeError("secret provider details")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "answer_events", fail)
    response = _client().post("/api/v1/answers/stream", json={"question": "Pourquoi ?"})
    events = [json.loads(line) for line in response.text.splitlines() if line]

    assert events[-1]["type"] == "error"
    assert "secret" not in response.text
    assert recorded["appended"][0][1][1][2]["status"] == "error"


def test_blank_or_oversized_questions_are_rejected_without_a_conversation(
    recorded: dict[str, Any],
) -> None:
    client = _client()
    for question in ("   ", "x" * 2001):
        events = _stream(client, {"question": question})
        assert [event["type"] for event in events] == ["error"]
    assert recorded["started"] == []


def test_scope_rejects_unknown_fields() -> None:
    response = _client().post(
        "/api/v1/answers/stream", json={"question": "q", "scope": {"teacher": "x"}}
    )
    assert response.status_code == 422


def _info(filename: str, kind: DocumentKind = "pdf") -> DocumentInfo:
    return DocumentInfo(
        id=str(DOCUMENT),
        subject="Général",
        course_id="stat",
        title="Fiche",
        source_filename=filename,
        kind=kind,
        page_count=1,
    )


def test_document_file_and_content_are_served_from_the_course_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "stat").mkdir()
    (tmp_path / "stat" / "fiche.md").write_text("# Fiche\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    monkeypatch.setattr(
        main, "get_document", lambda _factory, _id: _info("stat/fiche.md", "markdown")
    )
    client = _client(settings_factory=lambda: Settings(pdf_source_dir=tmp_path))

    file_response = client.get(f"/api/v1/documents/{DOCUMENT}/file")
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("text/markdown")
    assert file_response.headers["content-disposition"].startswith("inline")

    content = client.get(f"/api/v1/documents/{DOCUMENT}/content").json()
    assert content["kind"] == "markdown"
    assert content["lines"][0] == {"line": 1, "text": "# Fiche"}
    assert content["lines"][4] == {"line": 5, "text": "| 1 | 2 |"}


def test_documents_outside_the_course_folder_or_missing_are_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings_factory=lambda: Settings(pdf_source_dir=tmp_path))
    monkeypatch.setattr(main, "get_document", lambda _factory, _id: _info("../outside.pdf"))
    assert client.get(f"/api/v1/documents/{DOCUMENT}/file").status_code == 404

    def missing(_factory: object, _id: UUID) -> DocumentInfo:
        raise DocumentMissingError("gone")

    monkeypatch.setattr(main, "get_document", missing)
    assert client.get(f"/api/v1/documents/{DOCUMENT}").status_code == 404


def test_conversation_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.conversations import ConversationSummary

    summary = ConversationSummary(
        id=str(CONVERSATION), title="RSA", scope={}, updated_at=datetime(2026, 9, 28, tzinfo=UTC)
    )
    monkeypatch.setattr(main, "list_conversations", lambda _factory: [summary])
    monkeypatch.setattr(main, "get_conversation", lambda _factory, _id: None)
    monkeypatch.setattr(
        main,
        "rename_conversation",
        lambda _factory, _id, title: summary.model_copy(update={"title": title}),
    )
    deleted: list[UUID] = []

    def delete(_factory: object, conversation_id: UUID) -> bool:
        deleted.append(conversation_id)
        return True

    monkeypatch.setattr(main, "delete_conversation", delete)
    client = _client()

    assert client.get("/api/v1/conversations").json()["conversations"][0]["title"] == "RSA"
    assert client.get(f"/api/v1/conversations/{CONVERSATION}").status_code == 404
    assert (
        client.patch(f"/api/v1/conversations/{CONVERSATION}", json={"title": "AES"}).json()["title"]
        == "AES"
    )
    assert client.delete(f"/api/v1/conversations/{CONVERSATION}").status_code == 204
    assert deleted == [CONVERSATION]
