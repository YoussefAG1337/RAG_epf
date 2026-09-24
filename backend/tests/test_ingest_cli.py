"""CLI wiring tests for one-PDF ingestion."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app import ingest
from app.config import Settings
from app.ingestion import IngestionError, IngestionResult
from app.providers.deterministic import DeterministicEmbeddingProvider


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

    async def fake_ingest_pdf(**kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(ingest, "ingest_pdf", fake_ingest_pdf)

    exit_code = ingest.main(["--course-id", "course-a", "--pdf", "Cours1.pdf"])

    assert exit_code == 0
    assert captured["source_directory"] == tmp_path
    assert captured["selected_pdf"] == "Cours1.pdf"
    assert captured["course_id"] == "course-a"
    assert captured["session_factory"] is session_factory
    assert isinstance(captured["embedding_provider"], DeterministicEmbeddingProvider)
    assert engine.disposed
    output = capsys.readouterr()
    assert "ingested Cours1.pdf" in output.out
    assert "course=course-a pages=2 chunks=3" in output.out
    assert "physical page 2" in output.err


def test_cli_returns_nonzero_and_disposes_engine_on_ingestion_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    engine = _Engine()
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(pdf_source_dir=tmp_path))
    monkeypatch.setattr(ingest, "create_database_engine", lambda settings: engine)

    async def fail_ingestion(**kwargs):
        raise IngestionError("selected PDF is missing")

    monkeypatch.setattr(ingest, "ingest_pdf", fail_ingestion)

    exit_code = ingest.main(["--course-id", "course-a", "--pdf", "missing.pdf"])

    assert exit_code == 1
    assert engine.disposed
    assert "ingestion failed: selected PDF is missing" in capsys.readouterr().err
