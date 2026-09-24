"""Offline tests for safe single-PDF extraction and persistence."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import pytest
from pypdf import PdfWriter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    IngestionError,
    chunk_page_text,
    ingest_pdf,
)
from app.models import Document, DocumentChunk
from app.providers.deterministic import DeterministicEmbeddingProvider


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_pdf(page_texts: list[str]) -> bytes:
    """Create a small valid PDF with searchable text and optional blank pages."""

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    page_ids = [4 + 2 * index for index in range(len(page_texts))]
    objects[2] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_texts)} >>"
    ).encode("ascii")
    for index, page_text in enumerate(page_texts):
        page_id = page_ids[index]
        content_id = page_id + 1
        stream = (
            f"BT /F1 12 Tf 40 750 Td ({_escape_pdf_text(page_text)}) Tj ET" if page_text else ""
        ).encode("ascii")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, max(objects) + 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


class _FakeSession:
    def __init__(self, factory: _FakeSessionFactory) -> None:
        self.factory = factory
        self.document: Document | None = None
        self.chunks: list[DocumentChunk] = []

    def add(self, item: Any) -> None:
        if isinstance(item, Document):
            self.document = item

    def add_all(self, items: list[DocumentChunk]) -> None:
        if self.factory.fail_bulk:
            raise IntegrityError("insert", {}, RuntimeError("simulated database failure"))
        self.chunks.extend(items)


class _FakeTransaction(AbstractContextManager[_FakeSession]):
    def __init__(self, factory: _FakeSessionFactory) -> None:
        self.factory = factory
        self.session = _FakeSession(factory)

    def __enter__(self) -> _FakeSession:
        return self.session

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            if self.session.document is not None:
                self.factory.documents.append(self.session.document)
            self.factory.chunks.extend(self.session.chunks)
        else:
            self.factory.rollbacks += 1


class _FakeSessionFactory:
    def __init__(self, *, fail_bulk: bool = False) -> None:
        self.fail_bulk = fail_bulk
        self.documents: list[Document] = []
        self.chunks: list[DocumentChunk] = []
        self.rollbacks = 0

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self)


def _session_factory(factory: _FakeSessionFactory) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], factory)


async def _run_ingestion(
    source_directory: Path,
    selected_pdf: str,
    course_id: str,
    database: _FakeSessionFactory,
    *,
    provider: Any | None = None,
) -> Any:
    return await ingest_pdf(
        source_directory=source_directory,
        selected_pdf=selected_pdf,
        course_id=course_id,
        session_factory=_session_factory(database),
        embedding_provider=provider or DeterministicEmbeddingProvider(),
    )


def test_chunking_is_deterministic_bounded_and_page_local() -> None:
    text = ("First paragraph has a stable sentence. " * 50) + "\n\n" + ("Second paragraph. " * 45)

    first = chunk_page_text(text)
    second = chunk_page_text(text)

    assert first == second
    assert len(first) > 1
    assert all(len(chunk) <= DEFAULT_CHUNK_SIZE for chunk in first)
    assert DEFAULT_CHUNK_OVERLAP == 150
    assert all(
        previous[-DEFAULT_CHUNK_OVERLAP:] == following[:DEFAULT_CHUNK_OVERLAP]
        for previous, following in zip(first, first[1:], strict=False)
    )

    hard_split_text = "".join(chr(0x4E00 + index) for index in range(2600))
    hard_split_chunks = chunk_page_text(hard_split_text)
    assert [len(chunk) for chunk in hard_split_chunks] == [1000, 1000, 900]
    assert hard_split_chunks[0][-DEFAULT_CHUNK_OVERLAP:] == hard_split_chunks[1][
        :DEFAULT_CHUNK_OVERLAP
    ]


def test_ingest_extracts_text_pages_and_keeps_provenance(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(
        _text_pdf(["First page course text.", "", "Third page " + ("details. " * 180)])
    )
    database = _FakeSessionFactory()

    result = asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))

    assert result.page_count == 3
    assert result.empty_pages == (2,)
    assert result.chunk_count == len(database.chunks) >= 3
    assert {chunk.physical_page_number for chunk in database.chunks} == {1, 3}
    assert [chunk.chunk_position for chunk in database.chunks] == list(range(result.chunk_count))
    assert database.documents[0].checksum == result.checksum
    assert all(len(chunk.embedding) == 768 for chunk in database.chunks)
    assert all(chunk.course_id == "course-a" for chunk in database.chunks)


def test_same_pdf_persists_under_distinct_course_ids(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Shared course text."]))
    database = _FakeSessionFactory()

    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-b", database))

    assert [document.course_id for document in database.documents] == ["course-a", "course-b"]
    assert len({document.id for document in database.documents}) == 2
    assert {chunk.course_id for chunk in database.chunks} == {"course-a", "course-b"}
    assert len({chunk.id for chunk in database.chunks}) == 2


def test_document_and_chunk_ids_are_stable_for_same_course_source(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Stable course text."]))
    first_database = _FakeSessionFactory()
    second_database = _FakeSessionFactory()

    first = asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", first_database))
    second = asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", second_database))

    assert first.document_id == second.document_id
    assert [chunk.id for chunk in first_database.chunks] == [
        chunk.id for chunk in second_database.chunks
    ]


def test_pdf_with_no_usable_text_fails_before_opening_transaction(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "blank.pdf").write_bytes(_text_pdf(["", ""]))
    database = _FakeSessionFactory()

    with pytest.raises(IngestionError, match="no usable searchable text"):
        asyncio.run(_run_ingestion(source, "blank.pdf", "course-a", database))

    assert database.documents == []
    assert database.chunks == []


def test_invalid_or_escaping_source_is_rejected_before_database_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(_text_pdf(["outside text"]))
    database = _FakeSessionFactory()

    with pytest.raises(IngestionError, match="parent-directory traversal"):
        asyncio.run(_run_ingestion(source, "../outside.pdf", "course-a", database))
    with pytest.raises(IngestionError, match=".pdf extension"):
        asyncio.run(_run_ingestion(source, "notes.txt", "course-a", database))

    source_pdf = source / "escape.pdf"
    source_pdf.write_bytes(_text_pdf(["local text"]))
    original_resolve = Path.resolve

    def resolve_escape(path: Path, *, strict: bool = False) -> Path:
        if path == source_pdf:
            return outside.resolve()
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_escape)
    with pytest.raises(IngestionError, match="resolves outside"):
        asyncio.run(_run_ingestion(source, "escape.pdf", "course-a", database))

    assert database.documents == []
    assert database.chunks == []


def test_encrypted_or_malformed_pdf_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("test-password")
    encrypted = BytesIO()
    writer.write(encrypted)
    (source / "encrypted.pdf").write_bytes(encrypted.getvalue())
    (source / "broken.pdf").write_bytes(b"not a PDF")
    database = _FakeSessionFactory()

    with pytest.raises(IngestionError, match="encrypted"):
        asyncio.run(_run_ingestion(source, "encrypted.pdf", "course-a", database))
    with pytest.raises(IngestionError, match="could not read PDF"):
        asyncio.run(_run_ingestion(source, "broken.pdf", "course-a", database))
    assert database.documents == []


def test_embedding_or_persistence_failure_leaves_no_committed_rows(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Course text for a controlled failure."]))

    class BrokenEmbedder:
        async def embed(self, text: str) -> list[float]:
            raise RuntimeError("simulated provider error")

    provider_database = _FakeSessionFactory()
    with pytest.raises(IngestionError, match="embedding provider failed"):
        asyncio.run(
            _run_ingestion(
                source,
                "lesson.pdf",
                "course-a",
                provider_database,
                provider=BrokenEmbedder(),
            )
        )
    assert provider_database.documents == []

    persistence_database = _FakeSessionFactory(fail_bulk=True)
    with pytest.raises(IngestionError, match="database rejected"):
        asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", persistence_database))
    assert persistence_database.documents == []
    assert persistence_database.chunks == []
    assert persistence_database.rollbacks == 1


@pytest.mark.parametrize(
    ("vector", "message"),
    [([0.0, 1.0], "expected 768"), ([float("nan")] * 768, "non-finite")],
)
def test_invalid_embedding_vectors_are_rejected_before_transaction(
    tmp_path: Path, vector: list[float], message: str
) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Course text for invalid vector checks."]))

    class InvalidVectorProvider:
        async def embed(self, text: str) -> list[float]:
            return vector

    database = _FakeSessionFactory()
    with pytest.raises(IngestionError, match=message):
        asyncio.run(
            _run_ingestion(
                source,
                "lesson.pdf",
                "course-a",
                database,
                provider=InvalidVectorProvider(),
            )
        )

    assert database.documents == []
    assert database.chunks == []
    assert database.rollbacks == 0


def test_invalid_course_id_is_rejected(tmp_path: Path) -> None:
    database = _FakeSessionFactory()

    with pytest.raises(IngestionError, match="course ID must not be empty"):
        asyncio.run(_run_ingestion(tmp_path, "missing.pdf", "  ", database))
