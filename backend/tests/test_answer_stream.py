"""Offline stream endpoint contract tests."""

from fastapi.testclient import TestClient

from app.answering import AnswerCitation, AnswerClaim, GroundedAnswer
from app.main import create_app


def test_stream_emits_deltas_citations_then_one_completion(monkeypatch) -> None:
    answer_text = "Supported answer sentence with enough words to exceed eighty characters. " * 3

    async def answer_question(**_kwargs: object) -> GroundedAnswer:
        return GroundedAnswer(
            claims=[AnswerClaim(text=answer_text, citation_ids=["chunk-1"])],
            citations=[
                AnswerCitation(
                    citation_id="chunk-1",
                    source_filename="lesson.pdf",
                    physical_page_number=3,
                    excerpt="Source excerpt.",
                )
            ],
        )

    monkeypatch.setattr("app.main.answer_question", answer_question)
    events = [line for line in TestClient(create_app(schema_guard=None)).post(
        "/api/v1/answers/stream", json={"course_id": "course-1", "question": "Why?"}
    ).text.splitlines() if line]
    import json
    parsed = [json.loads(line) for line in events]
    assert [event["type"] for event in parsed] == [
        "delta", "delta", "delta", "citations", "completed"
    ]
    deltas = [event["text"] for event in parsed if event["type"] == "delta"]
    assert all(deltas)
    assert all(len(delta) <= 80 for delta in deltas)
    assert "".join(deltas) == answer_text
    assert parsed[-2]["citations"][0] == {
        "citation_id": "chunk-1", "source_filename": "lesson.pdf",
        "physical_page_number": 3, "excerpt": "Source excerpt.",
    }
    assert parsed[-1] == {"version": 1, "type": "completed"}


def test_stream_failure_is_one_safe_terminal_error(monkeypatch) -> None:
    async def fail(**_kwargs: object) -> GroundedAnswer:
        raise RuntimeError("secret details")

    monkeypatch.setattr("app.main.answer_question", fail)
    response = TestClient(create_app(schema_guard=None)).post(
        "/api/v1/answers/stream", json={"course_id": "course-1", "question": "Why?"}
    )
    import json
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "secret" not in response.text


def test_stream_separates_multiple_claims(monkeypatch) -> None:
    async def answer_question(**_kwargs: object) -> GroundedAnswer:
        return GroundedAnswer(
            claims=[
                AnswerClaim(text="First claim.", citation_ids=["chunk-1"]),
                AnswerClaim(text="Second claim.", citation_ids=["chunk-2"]),
            ],
            citations=[],
        )

    monkeypatch.setattr("app.main.answer_question", answer_question)
    response = TestClient(create_app(schema_guard=None)).post(
        "/api/v1/answers/stream", json={"course_id": "course-1", "question": "Why?"}
    )
    import json
    events = [json.loads(line) for line in response.text.splitlines() if line]
    streamed_text = "".join(
        event["text"] for event in events if event["type"] == "delta"
    )
    assert streamed_text == "First claim. Second claim."


def test_empty_stream_request_returns_safe_terminal_error() -> None:
    response = TestClient(create_app(schema_guard=None)).post(
        "/api/v1/answers/stream", json={"course_id": " ", "question": "Why?"}
    )
    import json
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "Check the course and question" in events[0]["message"]
