"""CLI wiring tests for one-PDF ingestion."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app import ingest
from app.config import Settings
from app.ingestion import IngestionError, IngestionResult
from app.providers.deterministic import DeterministicEmbeddingProvider
from app.providers.local import LocalEmbeddingProvider


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_cli_uses_configured_source_and_deterministic_provider_by_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = Settings(pdf_source_dir=tmp_path)
    engine = _Engine()
    session_factory = object()
    captured: dict[str, object] = {}
    result = IngestionResult(
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        source_filename="Cours1.pdf",
        checksum="abc123",
        page_count=2,
        chunk_count=3,
        empty_pages=(2,),
    )

    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "create_database_engine", lambda actual: engine)
    monkeypatch.setattr(ingest, "create_session_factory", lambda actual: session_factory)

    async def fake_ingest_document(**kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(ingest, "ingest_document", fake_ingest_document)

    exit_code = ingest.main(
        ["--subject", "subject-a", "--course-id", "course-a", "--pdf", "Cours1.pdf"]
    )

    assert exit_code == 0
    assert captured["source_directory"] == tmp_path
    assert captured["selected_file"] == "Cours1.pdf"
    assert captured["course_id"] == "course-a"
    assert captured["subject"] == "subject-a"
    assert captured["session_factory"] is session_factory
    assert isinstance(captured["embedding_provider"], DeterministicEmbeddingProvider)
    assert engine.disposed
    output = capsys.readouterr()
    assert "ingested Cours1.pdf" in output.out
    assert "subject=subject-a course=course-a pages=2 chunks=3" in output.out
    assert "Cours1.pdf page 2 contains no extractable text" in output.err


def test_cli_returns_nonzero_and_disposes_engine_on_ingestion_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    engine = _Engine()
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(pdf_source_dir=tmp_path))
    monkeypatch.setattr(ingest, "create_database_engine", lambda settings: engine)

    async def fail_ingestion(**kwargs):
        raise IngestionError("selected PDF is missing")

    monkeypatch.setattr(ingest, "ingest_document", fail_ingestion)

    exit_code = ingest.main(
        ["--subject", "subject-a", "--course-id", "course-a", "--pdf", "missing.pdf"]
    )

    assert exit_code == 1
    assert engine.disposed
    assert "ingestion failed: selected PDF is missing" in capsys.readouterr().err


def test_cli_uses_the_local_embeddings_service_and_provider_can_be_overridden(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = Settings(pdf_source_dir=tmp_path, embedding_provider="local")
    engine = _Engine()
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "create_database_engine", lambda _settings: engine)
    monkeypatch.setattr(ingest, "create_session_factory", lambda _engine: object())
    selected: list[object] = []

    async def fake_ingest_document(**kwargs):
        selected.append(kwargs["embedding_provider"])
        return IngestionResult(
            document_id=UUID("00000000-0000-0000-0000-000000000001"),
            source_filename="lesson.pdf",
            checksum="abc123",
            page_count=1,
            chunk_count=1,
            empty_pages=(),
        )

    monkeypatch.setattr(ingest, "ingest_document", fake_ingest_document)

    assert ingest.main(["--course-id", "course-a", "--file", "lesson.pdf"]) == 0
    assert isinstance(selected[-1], LocalEmbeddingProvider)
    assert engine.disposed

    engine = _Engine()
    monkeypatch.setattr(ingest, "create_database_engine", lambda _settings: engine)
    assert ingest.main(
        ["--course-id", "course-a", "--file", "lesson.pdf", "--provider", "deterministic"]
    ) == 0
    assert isinstance(selected[-1], DeterministicEmbeddingProvider)
    assert engine.disposed
    capsys.readouterr()


def test_cli_reports_invalid_configuration_clearly(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def missing_key_settings():
        return Settings(answer_provider="gemini")

    monkeypatch.setattr(ingest, "get_settings", missing_key_settings)
    monkeypatch.setattr(
        ingest,
        "create_database_engine",
        lambda _settings: pytest.fail("database must not be opened with invalid settings"),
    )

    assert ingest.main(["--course-id", "course-a", "--file", "lesson.pdf"]) == 1
    assert "ingestion failed: invalid runtime configuration" in capsys.readouterr().err


def test_cli_all_ingests_every_course_folder_and_reports_failures(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / "Info" / "course-a").mkdir(parents=True)
    (tmp_path / "Info" / "course-a" / "one.pdf").write_bytes(b"%PDF")
    (tmp_path / "Maths" / "course-b").mkdir(parents=True)
    (tmp_path / "Maths" / "course-b" / "broken.pdf").write_bytes(b"%PDF")
    (tmp_path / "loose.pdf").write_bytes(b"%PDF")
    engine = _Engine()
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(pdf_source_dir=tmp_path))
    monkeypatch.setattr(ingest, "create_database_engine", lambda _settings: engine)
    monkeypatch.setattr(ingest, "create_session_factory", lambda _engine: object())
    calls: list[tuple[str, str]] = []

    async def fake_ingest_document(**kwargs):
        calls.append((kwargs["subject"], kwargs["course_id"], kwargs["selected_file"]))
        if kwargs["selected_file"].endswith("broken.pdf"):
            raise IngestionError("PDF contains no usable searchable text")
        return IngestionResult(
            document_id=UUID("00000000-0000-0000-0000-000000000001"),
            source_filename=kwargs["selected_file"],
            checksum="abc123",
            page_count=1,
            chunk_count=1,
            empty_pages=(),
        )

    monkeypatch.setattr(ingest, "ingest_document", fake_ingest_document)

    assert ingest.main(["--all"]) == 1
    assert calls == [
        ("Info", "course-a", "Info/course-a/one.pdf"),
        ("Maths", "course-b", "Maths/course-b/broken.pdf"),
    ]
    assert engine.disposed
    output = capsys.readouterr()
    assert "ingested Info/course-a/one.pdf: subject=Info course=course-a" in output.out
    assert "ingested 1 of 2 files" in output.out
    assert "ingestion failed for Maths/course-b/broken.pdf" in output.err
    assert "skipped loose.pdf" in output.err


def test_cli_single_file_requires_course_id(capsys) -> None:
    with pytest.raises(SystemExit):
        ingest.main(["--subject", "Info", "--file", "lesson.pdf"])
    assert "--course-id is required" in capsys.readouterr().err
