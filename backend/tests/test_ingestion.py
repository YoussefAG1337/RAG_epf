"""Offline tests for safe single-PDF extraction and persistence."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Sequence
from contextlib import AbstractContextManager
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pymupdf
import pytest
from pypdf import PdfWriter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import EMBEDDING_DIMENSIONS
from app.ingestion import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SUBJECT,
    EMBEDDING_BATCH_SIZE,
    IngestionError,
    chunk_page_text,
    discover_course_files,
    ingest_document,
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
        self.deleted_ids: set[UUID] = set()

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def get(self, model: type[Any], identity: Any) -> Any:
        if model is Document:
            return next((item for item in self.factory.documents if item.id == identity), None)
        return None

    def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0].get("entity")
        if entity is Document:
            document_id = statement.whereclause.right.value
            return next((item for item in self.factory.documents if item.id == document_id), None)
        if entity is DocumentChunk:
            document_id = statement.whereclause.right.value
            return sum(chunk.document_id == document_id for chunk in self.factory.chunks)
        if statement.whereclause is not None:
            document_id = statement.whereclause.right.value
            return sum(chunk.document_id == document_id for chunk in self.factory.chunks)
        return None

    def scalars(self, statement: Any) -> Any:
        document_id = statement.whereclause.right.value
        rows = sorted(
            (chunk for chunk in self.factory.chunks if chunk.document_id == document_id),
            key=lambda chunk: chunk.chunk_position,
        )
        return type("Rows", (), {"all": lambda _self: rows})()

    def execute(self, statement: Any) -> Any:
        # Only the superseded-version delete is issued through execute.
        course_id, source_filename, kept_id = statement.compile().params.values()
        doomed = {
            item.id
            for item in self.factory.documents
            if item.course_id == course_id
            and item.source_filename == source_filename
            and item.id != kept_id
        }
        self.deleted_ids |= doomed
        return type("Result", (), {"rowcount": len(doomed)})()

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
        self.documents_before = copy.deepcopy(factory.documents)
        self.chunks_before = copy.deepcopy(factory.chunks)

    def __enter__(self) -> _FakeSession:
        return self.session

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None and self.factory.fail_commit:
            self.factory.documents[:] = self.documents_before
            self.factory.chunks[:] = self.chunks_before
            self.factory.rollbacks += 1
            raise IntegrityError("commit", {}, RuntimeError("simulated commit failure"))
        if exc_type is None:
            deleted = self.session.deleted_ids
            self.factory.documents[:] = [
                item for item in self.factory.documents if item.id not in deleted
            ]
            self.factory.chunks[:] = [
                chunk for chunk in self.factory.chunks if chunk.document_id not in deleted
            ]
            if self.session.document is not None:
                old = next(
                    (index for index, item in enumerate(self.factory.documents)
                     if item.id == self.session.document.id),
                    None,
                )
                if old is None:
                    self.factory.documents.append(self.session.document)
                else:
                    self.factory.documents[old] = self.session.document
            self.factory.chunks.extend(self.session.chunks)
        else:
            self.factory.rollbacks += 1


class _FakeSessionFactory:
    def __init__(self, *, fail_bulk: bool = False, fail_commit: bool = False) -> None:
        self.fail_bulk = fail_bulk
        self.fail_commit = fail_commit
        self.documents: list[Document] = []
        self.chunks: list[DocumentChunk] = []
        self.rollbacks = 0

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def __call__(self) -> _FakeSession:
        return _FakeSession(self)


def _slides_pdf(
    slides: Sequence[tuple[str | None, str]], *, footer: str | None = None
) -> bytes:
    """Create a 16:9 slide deck: large titles, wrapped body text, optional small footer."""

    document = pymupdf.open()
    for number, (title, body) in enumerate(slides, start=1):
        page = document.new_page(width=960, height=540)
        if title:
            page.insert_text((40, 60), title, fontsize=28)
        if body:
            page.insert_textbox(pymupdf.Rect(40, 100, 920, 500), body, fontsize=16)
        if footer:
            page.insert_text((40, 528), footer, fontsize=9)
            page.insert_text((900, 528), f"{number} / {len(slides)}", fontsize=9)
    return bytes(document.tobytes())


def _session_factory(factory: _FakeSessionFactory) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], factory)


async def _run_ingestion(
    source_directory: Path,
    selected_file: str,
    course_id: str,
    database: _FakeSessionFactory,
    *,
    provider: Any | None = None,
    subject: str = "subject-a",
) -> Any:
    return await ingest_document(
        source_directory=source_directory,
        selected_file=selected_file,
        subject=subject,
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
        _slides_pdf(
            [
                (None, "First page course text."),
                (None, ""),
                (None, "Third page " + ("details. " * 180)),
            ]
        )
    )
    database = _FakeSessionFactory()

    result = asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))

    assert result.page_count == 3
    assert result.empty_pages == (2,)
    assert result.chunk_count == len(database.chunks) >= 3
    assert {chunk.physical_page_number for chunk in database.chunks} == {1, 3}
    assert [chunk.chunk_position for chunk in database.chunks] == list(range(result.chunk_count))
    assert database.documents[0].checksum == result.checksum
    assert all(len(chunk.embedding) == EMBEDDING_DIMENSIONS for chunk in database.chunks)
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


def test_same_checksum_is_reembedded_when_provider_changes(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Semantic course content."]))
    database = _FakeSessionFactory()
    first = asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    old_chunk_text = [chunk.text for chunk in database.chunks]
    old_chunk_ids = [chunk.id for chunk in database.chunks]

    class GeminiLikeProvider:
        provider_id = "gemini"

        async def embed(self, _text: str) -> list[float]:
            return [0.25] * EMBEDDING_DIMENSIONS

    result = asyncio.run(
        _run_ingestion(source, "lesson.pdf", "course-a", database, provider=GeminiLikeProvider())
    )

    assert result.document_id == first.document_id
    assert len(database.documents) == 1
    assert database.documents[0].embedding_provider == "gemini"
    assert [chunk.text for chunk in database.chunks] == old_chunk_text
    assert [chunk.id for chunk in database.chunks] == old_chunk_ids
    assert all(chunk.embedding == [0.25] * EMBEDDING_DIMENSIONS for chunk in database.chunks)


def test_legacy_null_provider_is_reembedded_with_gemini_marker(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Legacy course content."]))
    database = _FakeSessionFactory()
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    old_ids = [chunk.id for chunk in database.chunks]
    database.documents[0].embedding_provider = None

    class GeminiLikeProvider:
        provider_id = "gemini"

        async def embed(self, _text: str) -> list[float]:
            return [0.5] * EMBEDDING_DIMENSIONS

    asyncio.run(
        _run_ingestion(source, "lesson.pdf", "course-a", database, provider=GeminiLikeProvider())
    )

    assert database.documents[0].embedding_provider == "gemini"
    assert [chunk.id for chunk in database.chunks] == old_ids
    assert all(chunk.embedding == [0.5] * EMBEDDING_DIMENSIONS for chunk in database.chunks)


def test_legacy_null_provider_failure_preserves_vectors_and_marker(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Legacy course content."]))
    database = _FakeSessionFactory()
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    old_vectors = [chunk.embedding[:] for chunk in database.chunks]
    database.documents[0].embedding_provider = None

    class BrokenGeminiLikeProvider:
        provider_id = "gemini"

        async def embed(self, _text: str) -> list[float]:
            raise RuntimeError("provider failure")

    with pytest.raises(IngestionError, match="embedding provider failed"):
        asyncio.run(
            _run_ingestion(
                source, "lesson.pdf", "course-a", database, provider=BrokenGeminiLikeProvider()
            )
        )

    assert database.documents[0].embedding_provider is None
    assert [chunk.embedding for chunk in database.chunks] == old_vectors


@pytest.mark.parametrize("corruption", ["count", "identity"])
def test_provider_change_refuses_chunk_count_or_identity_mismatch(
    tmp_path: Path, corruption: str
) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Course content for mismatch check."]))
    database = _FakeSessionFactory()
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    old_vectors = [chunk.embedding[:] for chunk in database.chunks]
    old_provider = database.documents[0].embedding_provider
    if corruption == "count":
        database.chunks.pop()
    else:
        database.chunks[0].id = UUID("00000000-0000-0000-0000-000000000099")
    old_rows = copy.deepcopy(database.chunks)

    class GeminiLikeProvider:
        provider_id = "gemini"

        async def embed(self, _text: str) -> list[float]:
            return [0.75] * EMBEDDING_DIMENSIONS

    with pytest.raises(IngestionError, match="chunks do not match"):
        asyncio.run(
            _run_ingestion(
                source, "lesson.pdf", "course-a", database, provider=GeminiLikeProvider()
            )
        )

    assert database.documents[0].embedding_provider == old_provider
    assert [
        (chunk.id, chunk.chunk_position, chunk.text, chunk.embedding)
        for chunk in database.chunks
    ] == [
        (chunk.id, chunk.chunk_position, chunk.text, chunk.embedding) for chunk in old_rows
    ]
    if corruption == "count":
        assert len(database.chunks) == len(old_vectors) - 1


def test_failed_provider_change_transaction_preserves_existing_vectors(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "lesson.pdf").write_bytes(_text_pdf(["Semantic course content."]))
    database = _FakeSessionFactory()
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    old_vectors = [chunk.embedding[:] for chunk in database.chunks]
    old_provider = database.documents[0].embedding_provider
    database.fail_commit = True

    class GeminiLikeProvider:
        provider_id = "gemini"

        async def embed(self, _text: str) -> list[float]:
            return [0.25] * EMBEDDING_DIMENSIONS

    with pytest.raises(IngestionError, match="transaction was rolled back"):
        asyncio.run(
            _run_ingestion(
                source, "lesson.pdf", "course-a", database, provider=GeminiLikeProvider()
            )
        )

    assert [chunk.embedding for chunk in database.chunks] == old_vectors
    assert database.documents[0].embedding_provider == old_provider


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
    with pytest.raises(IngestionError, match="one of these extensions"):
        asyncio.run(_run_ingestion(source, "notes.docx", "course-a", database))

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
    with pytest.raises(IngestionError, match="could not read broken.pdf"):
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
    [
        ([0.0, 1.0], f"expected {EMBEDDING_DIMENSIONS}"),
        ([float("nan")] * EMBEDDING_DIMENSIONS, "non-finite"),
    ],
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


def test_changed_pdf_replaces_its_previous_version_in_the_same_course(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    pdf = source / "lesson.pdf"
    database = _FakeSessionFactory()

    pdf.write_bytes(_text_pdf(["Original course text."]))
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))
    asyncio.run(_run_ingestion(source, "lesson.pdf", "course-b", database))
    pdf.write_bytes(_text_pdf(["Updated course text."]))
    result = asyncio.run(_run_ingestion(source, "lesson.pdf", "course-a", database))

    assert result.replaced_documents == 1
    course_a = [document for document in database.documents if document.course_id == "course-a"]
    assert [document.checksum for document in course_a] == [result.checksum]
    assert {chunk.text for chunk in database.chunks if chunk.course_id == "course-a"} == {
        "Updated course text."
    }
    # The same filename in another course is a different document and stays.
    assert any(document.course_id == "course-b" for document in database.documents)


def test_batch_capable_provider_is_called_in_bounded_batches(tmp_path: Path) -> None:
    source = tmp_path / "pdfs"
    source.mkdir()
    pages = [
        (None, f"Page {index} " + ("content. " * 20)) for index in range(EMBEDDING_BATCH_SIZE + 5)
    ]
    (source / "lesson.pdf").write_bytes(_slides_pdf(pages))
    batch_sizes: list[int] = []

    class BatchProvider:
        provider_id = "deterministic"

        async def embed(self, _text: str) -> list[float]:
            raise AssertionError("batch-capable providers must not be called per chunk")

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            batch_sizes.append(len(texts))
            return [[0.5] * EMBEDDING_DIMENSIONS for _ in texts]

    database = _FakeSessionFactory()
    result = asyncio.run(
        _run_ingestion(source, "lesson.pdf", "course-a", database, provider=BatchProvider())
    )

    assert batch_sizes == [EMBEDDING_BATCH_SIZE, 5]
    assert result.chunk_count == EMBEDDING_BATCH_SIZE + 5


def test_discover_course_files_reads_subject_and_course_folders(tmp_path: Path) -> None:
    (tmp_path / "Informatique" / "Algo 101" / "week1").mkdir(parents=True)
    (tmp_path / "Informatique" / "Algo 101" / "week1" / "intro.PDF").write_bytes(b"%PDF")
    (tmp_path / "Informatique" / "Algo 101" / "notes.md").write_text("# Notes")
    (tmp_path / "Informatique" / "Algo 101" / "photo.jpeg").write_bytes(b"jpeg")
    (tmp_path / "Maths" / "Analyse").mkdir(parents=True)
    (tmp_path / "Maths" / "Analyse" / "calcul.xlsx").write_bytes(b"xlsx")
    (tmp_path / "top.pdf").write_bytes(b"%PDF")

    selections, skipped = discover_course_files(tmp_path)
    assert [(item.subject, item.course_id, item.selected_file) for item in selections] == [
        ("Informatique", "Algo 101", "Informatique/Algo 101/notes.md"),
        ("Informatique", "Algo 101", "Informatique/Algo 101/week1/intro.PDF"),
        ("Maths", "Analyse", "Maths/Analyse/calcul.xlsx"),
    ]
    assert [(item.path, item.reason) for item in skipped] == [
        ("top.pdf", "not inside a course folder"),
        ("Informatique/Algo 101/photo.jpeg", "unsupported format .jpeg"),
    ]

    only_maths, _ = discover_course_files(tmp_path, subject="Maths")
    assert [item.course_id for item in only_maths] == ["Analyse"]
    only_algo, _ = discover_course_files(tmp_path, course_id="Algo 101")
    assert {item.subject for item in only_algo} == {"Informatique"}


def test_top_level_folders_holding_files_are_courses_under_the_default_subject(
    tmp_path: Path,
) -> None:
    (tmp_path / "Cryptographie" / "TD").mkdir(parents=True)
    (tmp_path / "Cryptographie" / "slides.pdf").write_bytes(b"%PDF")
    (tmp_path / "Cryptographie" / "TD" / "TD1.pdf").write_bytes(b"%PDF")
    (tmp_path / "stat").mkdir()
    (tmp_path / "stat" / "QCM.md").write_text("# QCM")

    selections, _ = discover_course_files(tmp_path)
    assert [(item.subject, item.course_id, item.selected_file) for item in selections] == [
        (DEFAULT_SUBJECT, "Cryptographie", "Cryptographie/TD/TD1.pdf"),
        (DEFAULT_SUBJECT, "Cryptographie", "Cryptographie/slides.pdf"),
        (DEFAULT_SUBJECT, "stat", "stat/QCM.md"),
    ]


def test_discover_course_files_rejects_a_course_under_two_subjects(tmp_path: Path) -> None:
    for subject in ("Informatique", "Maths"):
        (tmp_path / subject / "Projet").mkdir(parents=True)
        (tmp_path / subject / "Projet" / "slides.pdf").write_bytes(b"%PDF")

    with pytest.raises(IngestionError, match="unique across subjects"):
        discover_course_files(tmp_path)
