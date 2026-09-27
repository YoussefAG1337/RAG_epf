"""Single-PDF extraction, deterministic chunking, embedding, and persistence."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Document, DocumentChunk
from app.providers.protocols import EmbeddingProvider

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
EXPECTED_EMBEDDING_DIMENSIONS = 768


class IngestionError(RuntimeError):
    """A user-actionable error while validating or ingesting a source PDF."""


@dataclass(frozen=True)
class IngestionResult:
    """Summary of one successfully persisted source PDF."""

    document_id: UUID
    source_filename: str
    checksum: str
    page_count: int
    chunk_count: int
    empty_pages: tuple[int, ...]


@dataclass(frozen=True)
class _ChunkDraft:
    physical_page_number: int
    chunk_position: int
    text: str


def validate_course_id(course_id: str) -> str:
    """Return a normalized course ID that fits the persistence contract."""

    normalized = course_id.strip()
    if not normalized:
        raise IngestionError("course ID must not be empty")
    if len(normalized) > 200:
        raise IngestionError("course ID must be no more than 200 characters")
    return normalized


def resolve_selected_pdf(source_directory: Path, selected_pdf: str) -> tuple[Path, str]:
    """Resolve exactly one relative PDF and reject paths escaping the configured root."""

    try:
        source_root = source_directory.expanduser().resolve(strict=True)
    except OSError as error:
        raise IngestionError(f"PDF source directory is unavailable: {source_directory}") from error
    if not source_root.is_dir():
        raise IngestionError(f"PDF source path is not a directory: {source_root}")

    relative_path = Path(selected_pdf)
    if relative_path.is_absolute() or not selected_pdf.strip():
        raise IngestionError(
            "PDF selection must be a relative path beneath the configured source directory"
        )
    if ".." in relative_path.parts:
        raise IngestionError("PDF selection must not contain parent-directory traversal")
    if relative_path.suffix.lower() != ".pdf":
        raise IngestionError("selected source must have a .pdf extension")

    candidate = source_root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
        relative_identity = resolved.relative_to(source_root).as_posix()
    except (OSError, RuntimeError, ValueError) as error:
        raise IngestionError(
            "selected PDF is missing or resolves outside the configured source directory"
        ) from error
    if not resolved.is_file():
        raise IngestionError("selected PDF is not a regular file")
    if len(relative_identity) > 512:
        raise IngestionError("selected PDF path must be no more than 512 characters")
    return resolved, relative_identity


def normalize_page_text(text: str) -> str:
    """Normalize extracted text consistently while retaining paragraph boundaries."""

    normalized = unicodedata.normalize("NFC", text).replace("\x00", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[str] = []
    current_lines: list[str] = []
    for line in normalized.split("\n"):
        compact = re.sub(r"[\t\f\v ]+", " ", line).strip()
        if compact:
            current_lines.append(compact)
        elif current_lines:
            paragraphs.append(" ".join(current_lines))
            current_lines = []
    if current_lines:
        paragraphs.append(" ".join(current_lines))
    return "\n\n".join(paragraphs)


def _preferred_end(text: str, start: int, hard_end: int, chunk_size: int) -> int:
    """Choose a paragraph, sentence, or word boundary near the chunk limit."""

    minimum = start + max(1, chunk_size // 2)
    paragraph_end = text.rfind("\n\n", minimum, hard_end)
    if paragraph_end >= minimum:
        return paragraph_end

    section = text[start:hard_end]
    sentence_ends = [
        start + match.end()
        for match in re.finditer(r"[.!?][\"')\]]*\s+", section)
    ]
    valid_sentence_ends = [position for position in sentence_ends if position >= minimum]
    if valid_sentence_ends:
        return valid_sentence_ends[-1]

    word_end = text.rfind(" ", minimum, hard_end)
    return word_end if word_end >= minimum else hard_end


def chunk_page_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split one page deterministically with bounded overlap and no page crossing."""

    if chunk_size < 1:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between zero and chunk_size - 1")

    page_text = normalize_page_text(text)
    if not page_text:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(page_text):
        hard_end = min(start + chunk_size, len(page_text))
        end = hard_end if hard_end == len(page_text) else _preferred_end(
            page_text, start, hard_end, chunk_size
        )
        chunk = page_text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(page_text):
            break

        next_start = max(start + 1, end - overlap)
        if next_start <= start or next_start >= len(page_text):
            break
        start = next_start
    return chunks


def _read_pdf(
    data: bytes, source_filename: str
) -> tuple[int, list[tuple[int, str]], tuple[int, ...]]:
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise IngestionError(f"PDF is encrypted and cannot be ingested: {source_filename}")
        page_count = len(reader.pages)
        pages: list[tuple[int, str]] = []
        empty_pages: list[int] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = normalize_page_text(page.extract_text() or "")
            except (PdfReadError, ValueError) as error:
                raise IngestionError(
                    f"failed to extract physical page {index} from {source_filename}"
                ) from error
            if text:
                pages.append((index, text))
            else:
                empty_pages.append(index)
    except IngestionError:
        raise
    except (PdfReadError, OSError, ValueError) as error:
        raise IngestionError(f"could not read PDF {source_filename}: {error}") from error
    if page_count == 0:
        raise IngestionError(f"PDF has no pages: {source_filename}")
    if not pages:
        raise IngestionError(f"PDF contains no usable searchable text: {source_filename}")
    return page_count, pages, tuple(empty_pages)


async def ingest_pdf(
    *,
    source_directory: Path,
    selected_pdf: str,
    course_id: str,
    session_factory: sessionmaker[Session],
    embedding_provider: EmbeddingProvider,
    display_filename: str | None = None,
) -> IngestionResult:
    """Ingest one selected PDF and atomically persist its course-scoped vectors."""

    normalized_course_id = validate_course_id(course_id)
    resolved_pdf, stored_filename = resolve_selected_pdf(source_directory, selected_pdf)
    source_filename = display_filename or stored_filename
    if not source_filename.strip() or len(source_filename) > 512:
        raise IngestionError("source filename must be between 1 and 512 characters")
    try:
        pdf_bytes = resolved_pdf.read_bytes()
    except OSError as error:
        raise IngestionError(f"could not read selected PDF: {source_filename}") from error

    checksum = hashlib.sha256(pdf_bytes).hexdigest()
    document_id = uuid5(
        NAMESPACE_URL,
        f"course={normalized_course_id}\nsource={source_filename}\nsha256={checksum}",
    )
    provider_id = getattr(embedding_provider, "provider_id", type(embedding_provider).__name__)
    with session_factory() as session:
        existing = session.get(Document, document_id)
        if existing is not None and existing.embedding_provider == provider_id:
            return IngestionResult(
                document_id=document_id,
                source_filename=source_filename,
                checksum=checksum,
                page_count=existing.page_count,
                chunk_count=session.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                ) or 0,
                empty_pages=(),
            )
    page_count, pages, empty_pages = _read_pdf(pdf_bytes, source_filename)
    drafts: list[_ChunkDraft] = []
    for physical_page_number, text in pages:
        for chunk in chunk_page_text(text):
            drafts.append(
                _ChunkDraft(
                    physical_page_number=physical_page_number,
                    chunk_position=len(drafts),
                    text=chunk,
                )
            )
    if not drafts:
        raise IngestionError(f"PDF contains no usable searchable text: {source_filename}")

    vectors: list[list[float]] = []
    for draft in drafts:
        try:
            vector = await embedding_provider.embed(draft.text)
        except Exception as error:
            raise IngestionError("embedding provider failed; no records were written") from error
        if len(vector) != EXPECTED_EMBEDDING_DIMENSIONS:
            raise IngestionError(
                "embedding provider returned "
                f"{len(vector)} dimensions; expected {EXPECTED_EMBEDDING_DIMENSIONS}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise IngestionError("embedding provider returned a non-finite vector value")
        vectors.append(vector)

    document = Document(
        id=document_id,
        course_id=normalized_course_id,
        source_filename=source_filename,
        checksum=checksum,
        page_count=page_count,
        embedding_provider=provider_id,
    )
    chunks = [
        DocumentChunk(
            id=uuid5(
                NAMESPACE_URL,
                f"document={document_id}\nposition={draft.chunk_position}\n"
                f"page={draft.physical_page_number}\ntext={hashlib.sha256(draft.text.encode('utf-8')).hexdigest()}",
            ),
            document_id=document_id,
            course_id=normalized_course_id,
            physical_page_number=draft.physical_page_number,
            chunk_position=draft.chunk_position,
            text=draft.text,
            embedding=vector,
        )
        for draft, vector in zip(drafts, vectors, strict=True)
    ]

    try:
        with session_factory.begin() as session:
            current = session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if current is None:
                session.add(document)
                session.add_all(chunks)
            elif current.embedding_provider != provider_id:
                persisted_chunks = session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.chunk_position)
                    .with_for_update()
                ).all()
                if len(persisted_chunks) != len(chunks):
                    raise IngestionError(
                        "stored document chunks do not match the source; refusing provider update"
                    )
                for persisted, replacement in zip(persisted_chunks, chunks, strict=True):
                    if (
                        persisted.id != replacement.id
                        or persisted.chunk_position != replacement.chunk_position
                    ):
                        raise IngestionError(
                            "stored document chunks do not match the source; "
                            "refusing provider update"
                        )
                    persisted.embedding = replacement.embedding
                current.embedding_provider = provider_id
    except IntegrityError as error:
        raise IngestionError(
            "database rejected this document; the transaction was rolled back"
        ) from error
    except SQLAlchemyError as error:
        raise IngestionError(
            "database persistence failed; the transaction was rolled back"
        ) from error

    return IngestionResult(
        document_id=document_id,
        source_filename=source_filename,
        checksum=checksum,
        page_count=page_count,
        chunk_count=len(chunks),
        empty_pages=empty_pages,
    )
