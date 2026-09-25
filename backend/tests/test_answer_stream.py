"""Offline stream endpoint contract tests."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.answering import AnswerCitation, AnswerClaim, GroundedAnswer
from app.config import Settings
from app.main import create_app
from app.providers.gemini import GeminiEmbeddingProvider, GeminiGenerationProvider


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


def test_gemini_runtime_selects_matching_embedding_and_generation_adapters(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def answer_question(**kwargs: object) -> GroundedAnswer:
        captured.update(kwargs)
        return GroundedAnswer(claims=[], citations=[])

    monkeypatch.setattr("app.main.answer_question", answer_question)
    settings = Settings(rag_provider="gemini", gemini_api_key=SecretStr("server-test-key"))
    with TestClient(create_app(schema_guard=None, settings_factory=lambda: settings)) as client:
        response = client.post(
            "/api/v1/answers/stream", json={"course_id": "course-a", "question": "Question?"}
        )

    assert response.status_code == 200
    assert isinstance(captured["embedding_provider"], GeminiEmbeddingProvider)
    assert isinstance(captured["generation_provider"], GeminiGenerationProvider)


def test_gemini_adapters_run_real_answer_flow_and_stream_same_course_citation() -> None:
    chunk_id = UUID("00000000-0000-0000-0000-000000000123")
    calls: list[tuple[str, str]] = []

    class FakeModels:
        async def embed_content(self, *, model, contents, config):
            calls.append(("embed", model))
            assert contents == "What is taught?"
            assert config.output_dimensionality == 768
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.125] * 768)])

        async def generate_content(self, *, model, contents, config):
            calls.append(("generate", model))
            evidence = json.loads(contents.rsplit("Evidence:\n", maxsplit=1)[1])
            response = json.dumps(
                {
                    "claims": [
                        {"text": "The material teaches semantic retrieval.",
                         "citation_ids": [evidence[0]["citation_id"]]}
                    ]
                }
            )
            return SimpleNamespace(text=response)

    class FakeSDKClient:
        def __init__(self) -> None:
            self.aio = SimpleNamespace(models=FakeModels())

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            compiled = statement.compile()
            assert "document_chunks.course_id" in str(compiled)
            assert "course-a" in compiled.params.values()
            row = SimpleNamespace(
                id=chunk_id,
                physical_page_number=4,
                text="The material teaches semantic retrieval.",
                source_filename="lesson.pdf",
                distance=0.05,
            )
            return SimpleNamespace(all=lambda: [row])

    class FakeSessionFactory:
        def __call__(self):
            return FakeSession()

    sdk_client = FakeSDKClient()
    settings = Settings(rag_provider="gemini", gemini_api_key=SecretStr("server-test-key"))
    app = create_app(
        schema_guard=None,
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        embedding_provider=GeminiEmbeddingProvider(
            settings.gemini_api_key, client=sdk_client  # type: ignore[arg-type]
        ),
        generation_provider=GeminiGenerationProvider(
            settings.gemini_api_key, client=sdk_client  # type: ignore[arg-type]
        ),
        settings_factory=lambda: settings,
    )
    response = TestClient(app).post(
        "/api/v1/answers/stream",
        json={"course_id": "course-a", "question": "What is taught?"},
    )
    events = [json.loads(line) for line in response.text.splitlines() if line]

    assert calls == [("embed", "gemini-embedding-2"), ("generate", "gemini-3.8-flash")]
    assert [event["type"] for event in events][-2:] == ["citations", "completed"]
    assert "".join(event["text"] for event in events if event["type"] == "delta") == (
        "The material teaches semantic retrieval."
    )
    assert events[-2]["citations"] == [
        {
            "citation_id": str(chunk_id),
            "source_filename": "lesson.pdf",
            "physical_page_number": 4,
            "excerpt": "The material teaches semantic retrieval.",
        }
    ]


def test_gemini_providers_close_when_schema_validation_fails(monkeypatch) -> None:
    closed: list[str] = []

    class ClosableProvider:
        def __init__(self, label: str) -> None:
            self.label = label

        def close(self) -> None:
            closed.append(self.label)

    monkeypatch.setattr(
        "app.main.GeminiEmbeddingProvider", lambda *_args, **_kwargs: ClosableProvider("embed")
    )
    monkeypatch.setattr(
        "app.main.GeminiGenerationProvider", lambda *_args, **_kwargs: ClosableProvider("generate")
    )

    def fail_schema(_connection, _settings) -> None:
        raise RuntimeError("schema mismatch")

    settings = Settings(rag_provider="gemini", gemini_api_key=SecretStr("server-test-key"))
    app = create_app(schema_guard=fail_schema, settings_factory=lambda: settings)

    with pytest.raises(RuntimeError, match="schema mismatch"), TestClient(app):
        pass

    assert closed == ["embed", "generate"]
