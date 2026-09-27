"""PDF extraction, slide-aware chunking, contextual embedding, and persistence.

Course material is organized as subject > course > document > page (slide). Each chunk is
embedded together with that path and its section title, so a slide that only says
"Insertion : O(1)" is still retrievable as part of "Algorithmique > Listes chaînées".
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Document, DocumentChunk
from app.parsed import ParsedDocument, ParsedPage
from app.pdf_parsing import EncryptedPdfError, PdfParsingError, parse_pdf
from app.providers.protocols import EmbeddingProvider
from app.text_parsing import TextParsingError, parse_markdown, parse_xlsx

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
EXPECTED_EMBEDDING_DIMENSIONS = 768
# Gemini's free tier allows 100 embedded texts per minute; small batches let ingestion wait
# out the quota window in steps instead of failing on one oversized request.
EMBEDDING_BATCH_SIZE = 25
# Bump when parsing or chunking changes so re-ingestion rebuilds existing documents.
PARSER_VERSION = 4
SUPPORTED_SUFFIXES = frozenset({".pdf", ".md", ".markdown", ".txt", ".xlsx"})
# Subject given to course folders placed directly beneath the source directory.
DEFAULT_SUBJECT = "Général"
# A section-style title may carry a short subtitle of at most this length.
DIVIDER_MAX_BODY_CHARACTERS = 60
# A title-only page mostly covered by an image is a diagram, not a divider.
DIVIDER_MAX_IMAGE_SHARE = 0.1
_SECTION_HEADING = re.compile(
    r"^(chapitre|chapter|partie|part|section|module|s[ée]ance|le[çc]on|lesson|th[èe]me"
    r"|unit[ée]?)\b"
    r"|^[IVXLC]+[.)]\s|^\d+(\.\d+)*[.)]?\s",
    re.IGNORECASE,
)


class IngestionError(RuntimeError):
    """A user-actionable error while validating or ingesting a source file."""


@dataclass(frozen=True)
class IngestionResult:
    """Summary of one successfully persisted source file."""

    document_id: UUID
    source_filename: str
    checksum: str
    page_count: int
    chunk_count: int
    empty_pages: tuple[int, ...]
    replaced_documents: int = 0


@dataclass(frozen=True)
class CourseSelection:
    """One supported file found in the course folder layout."""

    subject: str
    course_id: str
    selected_file: str


@dataclass(frozen=True)
class SkippedFile:
    """A file the folder scan left out, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class _ChunkDraft:
    physical_page_number: int
    chunk_position: int
    section_title: str | None
    text: str


def _validate_name(value: str, label: str) -> str:
    # macOS stores folder names decomposed (NFD); normalize so names compare equal.
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise IngestionError(f"{label} must not be empty")
    if len(normalized) > 200:
        raise IngestionError(f"{label} must be no more than 200 characters")
    return normalized


def validate_course_id(course_id: str) -> str:
    """Return a normalized course ID that fits the persistence contract."""

    return _validate_name(course_id, "course ID")


def validate_subject(subject: str) -> str:
    """Return a normalized subject name that fits the persistence contract."""

    return _validate_name(subject, "subject")


def resolve_selected_file(source_directory: Path, selected_file: str) -> tuple[Path, str]:
    """Resolve exactly one relative source file and reject paths escaping the root."""

    try:
        source_root = source_directory.expanduser().resolve(strict=True)
    except OSError as error:
        raise IngestionError(f"source directory is unavailable: {source_directory}") from error
    if not source_root.is_dir():
        raise IngestionError(f"source path is not a directory: {source_root}")

    relative_path = Path(selected_file)
    if relative_path.is_absolute() or not selected_file.strip():
        raise IngestionError(
            "file selection must be a relative path beneath the configured source directory"
        )
    if ".." in relative_path.parts:
        raise IngestionError("file selection must not contain parent-directory traversal")
    if relative_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise IngestionError(
            "selected source must have one of these extensions: "
            + ", ".join(sorted(SUPPORTED_SUFFIXES))
        )

    candidate = source_root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
        relative_identity = resolved.relative_to(source_root).as_posix()
    except (OSError, RuntimeError, ValueError) as error:
        raise IngestionError(
            "selected file is missing or resolves outside the configured source directory"
        ) from error
    if not resolved.is_file():
        raise IngestionError("selected file is not a regular file")
    if len(relative_identity) > 512:
        raise IngestionError("selected file path must be no more than 512 characters")
    return resolved, relative_identity


def discover_course_files(
    source_directory: Path, *, subject: str | None = None, course_id: str | None = None
) -> tuple[list[CourseSelection], list[SkippedFile]]:
    """Find supported files in either folder layout beneath the source directory.

    * ``<course>/**/<file>``: a top-level folder that directly contains files is a course,
      filed under the default subject; its subfolders just organize that course.
    * ``<subject>/<course>/**/<file>``: a top-level folder containing only folders is a
      subject, and each of its folders is a course.

    Returns the selections and the files left out (loose files, unsupported formats).
    ``subject`` and ``course_id`` narrow the scan. Course names must be unique.
    """

    try:
        source_root = source_directory.expanduser().resolve(strict=True)
    except OSError as error:
        raise IngestionError(f"source directory is unavailable: {source_directory}") from error
    if not source_root.is_dir():
        raise IngestionError(f"source path is not a directory: {source_root}")

    wanted_subject = None if subject is None else validate_subject(subject)
    wanted_course = None if course_id is None else validate_course_id(course_id)

    def visible(path: Path) -> bool:
        return not path.name.startswith(".")

    def files_beneath(folder: Path) -> list[Path]:
        return sorted(
            path
            for path in folder.rglob("*")
            if path.is_file()
            and all(visible(Path(part)) for part in path.relative_to(source_root).parts)
        )

    courses: list[tuple[str, str, Path]] = []  # (subject, course, folder)
    skipped: list[SkippedFile] = []
    for top in sorted(path for path in source_root.iterdir() if visible(path)):
        if top.is_file():
            skipped.append(SkippedFile(top.name, "not inside a course folder"))
        elif any(child.is_file() and visible(child) for child in top.iterdir()):
            courses.append((DEFAULT_SUBJECT, validate_course_id(top.name), top))
        else:
            for course_folder in sorted(child for child in top.iterdir() if visible(child)):
                courses.append(
                    (
                        validate_subject(top.name),
                        validate_course_id(course_folder.name),
                        course_folder,
                    )
                )

    course_subjects: dict[str, str] = {}
    selections: list[CourseSelection] = []
    for folder_subject, folder_course, folder in courses:
        known_subject = course_subjects.setdefault(folder_course, folder_subject)
        if known_subject != folder_subject:
            raise IngestionError(
                f"course {folder_course!r} appears under subjects {known_subject!r} and "
                f"{folder_subject!r}; course folder names must be unique across subjects"
            )
        if wanted_subject is not None and folder_subject != wanted_subject:
            continue
        if wanted_course is not None and folder_course != wanted_course:
            continue
        for path in files_beneath(folder):
            relative = path.relative_to(source_root).as_posix()
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                reason = f"unsupported format {path.suffix or '(none)'}"
                skipped.append(SkippedFile(relative, reason))
                continue
            selections.append(
                CourseSelection(
                    subject=folder_subject, course_id=folder_course, selected_file=relative
                )
            )
    return selections, skipped


def normalize_page_text(text: str) -> str:
    """Normalize extracted text while retaining line (bullet) and paragraph boundaries."""

    normalized = unicodedata.normalize("NFC", text).replace("\x00", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[str] = []
    current_lines: list[str] = []
    for line in normalized.split("\n"):
        compact = re.sub(r"[\t\f\v ]+", " ", line).strip()
        if compact:
            current_lines.append(compact)
        elif current_lines:
            paragraphs.append("\n".join(current_lines))
            current_lines = []
    if current_lines:
        paragraphs.append("\n".join(current_lines))
    return "\n\n".join(paragraphs)


def _preferred_end(text: str, start: int, hard_end: int, chunk_size: int) -> int:
    """Choose a paragraph, line (bullet), sentence, or word boundary near the chunk limit."""

    minimum = start + max(1, chunk_size // 2)
    paragraph_end = text.rfind("\n\n", minimum, hard_end)
    if paragraph_end >= minimum:
        return paragraph_end
    line_end = text.rfind("\n", minimum, hard_end)
    if line_end >= minimum:
        return line_end

    section = text[start:hard_end]
    sentence_ends = [
        start + match.end()
        for match in re.finditer(r"[.!?][\"')\]]*\s+", section)
    ]
    valid_sentence_ends = [position for position in sentence_ends if position >= minimum]
    if valid_sentence_ends:
        return valid_sentence_ends[-1]

    word_end = max(text.rfind(" ", minimum, hard_end), text.rfind("\n", minimum, hard_end))
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


_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}")


def chunk_lines(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Pack whole lines (bullets, table rows, paragraphs) into chunks of bounded size.

    Lines are never cut unless one alone exceeds the size. A chunk that starts inside a
    Markdown table repeats the table's header rows so its columns stay meaningful. Up to
    ``overlap`` characters of trailing non-table lines are repeated in the next chunk.
    """

    normalized = normalize_page_text(text)
    if not normalized:
        return []
    lines: list[str] = []
    for line in normalized.split("\n"):
        lines.extend(chunk_page_text(line, chunk_size=chunk_size, overlap=overlap) or [line])

    chunks: list[list[str]] = []
    current: list[str] = []
    table_header: list[str] = []
    for index, line in enumerate(lines):
        if _TABLE_ROW.match(line):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if _TABLE_SEPARATOR.match(following):
                table_header = [line, following]
        elif line.strip():
            table_header = []
        if current and len("\n".join([*current, line])) > chunk_size:
            chunks.append(current)
            carried: list[str] = []
            for previous in reversed(current):
                if _TABLE_ROW.match(previous) or len("\n".join([previous, *carried])) > overlap:
                    break
                carried.insert(0, previous)
            in_table = _TABLE_ROW.match(line) and table_header and line not in table_header
            current = [*(table_header if in_table else []), *carried]
        current.append(line)
    if current:
        chunks.append(current)
    return ["\n".join(chunk).strip() for chunk in chunks if "\n".join(chunk).strip()]


def _parse(data: bytes, source_filename: str) -> ParsedDocument:
    suffix = Path(source_filename).suffix.lower()
    try:
        if suffix == ".pdf":
            parsed = parse_pdf(data)
        elif suffix == ".xlsx":
            parsed = parse_xlsx(data)
        else:
            parsed = parse_markdown(data)
    except EncryptedPdfError as error:
        raise IngestionError(
            f"PDF is encrypted and cannot be ingested: {source_filename}"
        ) from error
    except (PdfParsingError, TextParsingError) as error:
        raise IngestionError(f"could not read {source_filename}: {error}") from error
    if parsed.page_count == 0:
        raise IngestionError(f"file has no pages: {source_filename}")
    if not parsed.pages:
        raise IngestionError(
            f"file contains no usable searchable text (scanned PDFs need OCR): {source_filename}"
        )
    return parsed


def title_from_filename(source_filename: str) -> str:
    """Turn ``week-2/03_listes_chainees.pdf`` into ``03 listes chainees``."""

    stem = Path(source_filename).name
    while Path(stem).suffix.lower() in {*SUPPORTED_SUFFIXES, ".pptx", ".docx"}:
        stem = Path(stem).stem  # "EPF Statistics.pptx.pdf" -> "EPF Statistics"
    return re.sub(r"[_\s]+", " ", stem).strip() or stem


def _draft_chunks(parsed: ParsedDocument) -> list[_ChunkDraft]:
    """Make chunks per heading segment (one per slide, exercise, question, or section).

    Each chunk records its heading path. Text that continues onto a new page without a
    heading keeps the previous heading, and slide section dividers set the section of the
    slides that follow them.
    """

    drafts: list[_ChunkDraft] = []
    section: str | None = None
    current_path: tuple[str, ...] = ()
    kinds = {page.number: _divider_kind(page) for page in parsed.pages} if parsed.paginated else {}
    # A lone untitled-pattern divider ("Practice") in a deck that otherwise has no structure
    # would wrongly label every later slide, so plain dividers need company.
    plain_dividers_count = sum(kind == "plain" for kind in kinds.values()) >= 2
    for page in parsed.pages:
        kind = kinds.get(page.number)
        if kind == "named" or (kind == "plain" and plain_dividers_count):
            section, current_path = page.title, ()
        has_text = any(segment.text for segment in page.segments)
        for segment in page.segments:
            if segment.heading_path:
                current_path = segment.heading_path
            if not segment.text and has_text:
                continue  # a heading whose content is in the segments that follow
            text = "\n".join(part for part in (segment.heading, segment.text) if part)
            parts: list[str] = [section] if section else []
            parts.extend(part for part in current_path if part not in parts)
            heading = " > ".join(parts) or None
            for chunk in chunk_lines(text):
                drafts.append(
                    _ChunkDraft(
                        physical_page_number=page.number,
                        chunk_position=len(drafts),
                        section_title=heading,
                        text=chunk,
                    )
                )
    return drafts


def _divider_kind(page: ParsedPage) -> str | None:
    """Classify a page as a section divider.

    "named": a "Chapitre 2"/"II."-style title with at most a short subtitle and no bullets.
    "plain": any other title after the cover with no content and no large picture.
    """

    if page.number == 1 or not page.title or len(page.segments) != 1:
        return None
    named = _SECTION_HEADING.match(page.title) is not None
    if (
        named
        and len(page.body) <= DIVIDER_MAX_BODY_CHARACTERS
        and not any(line.startswith("- ") for line in page.body.splitlines())
    ):
        return "named"
    if not page.body and page.image_share < DIVIDER_MAX_IMAGE_SHARE:
        return "plain"
    return None


def contextual_embedding_text(
    *,
    subject: str,
    course_id: str,
    document_title: str,
    section_title: str | None,
    text: str,
) -> str:
    """Prefix a chunk with where it sits in the course material before embedding it."""

    path = " > ".join(part for part in (subject, course_id, document_title, section_title) if part)
    return f"{path}\n\n{text}"


@dataclass(frozen=True)
class DocumentPreview:
    """What ingestion would store for one file, without touching the database."""

    source_filename: str
    title: str
    page_count: int
    empty_pages: tuple[int, ...]
    paginated: bool
    chunks: tuple[tuple[int, str | None, str], ...]  # (page, section, text)
    embedding_inputs: tuple[str, ...]


def _read_source(source_directory: Path, selected_file: str) -> tuple[bytes, str]:
    resolved, source_filename = resolve_selected_file(source_directory, selected_file)
    try:
        return resolved.read_bytes(), source_filename
    except OSError as error:
        raise IngestionError(f"could not read selected file: {source_filename}") from error


def _prepare(
    data: bytes, source_filename: str, subject: str, course_id: str
) -> tuple[ParsedDocument, str, list[_ChunkDraft], list[str]]:
    parsed = _parse(data, source_filename)
    document_title = parsed.title or title_from_filename(source_filename)
    drafts = _draft_chunks(parsed)
    if not drafts:
        raise IngestionError(f"file contains no usable searchable text: {source_filename}")
    embedding_inputs = [
        contextual_embedding_text(
            subject=subject,
            course_id=course_id,
            document_title=document_title,
            section_title=draft.section_title,
            text=draft.text,
        )
        for draft in drafts
    ]
    return parsed, document_title, drafts, embedding_inputs


def preview_document(
    *, source_directory: Path, selected_file: str, subject: str, course_id: str
) -> DocumentPreview:
    """Parse and chunk one file exactly as ingestion would, for inspection."""

    data, source_filename = _read_source(source_directory, selected_file)
    parsed, title, drafts, embedding_inputs = _prepare(
        data, source_filename, validate_subject(subject), validate_course_id(course_id)
    )
    return DocumentPreview(
        source_filename=source_filename,
        title=title,
        page_count=parsed.page_count,
        empty_pages=parsed.empty_pages,
        paginated=parsed.paginated,
        chunks=tuple(
            (draft.physical_page_number, draft.section_title, draft.text) for draft in drafts
        ),
        embedding_inputs=tuple(embedding_inputs),
    )


async def ingest_document(
    *,
    source_directory: Path,
    selected_file: str,
    subject: str,
    course_id: str,
    session_factory: sessionmaker[Session],
    embedding_provider: EmbeddingProvider,
) -> IngestionResult:
    """Ingest one selected file and atomically persist its course-scoped vectors."""

    normalized_subject = validate_subject(subject)
    normalized_course_id = validate_course_id(course_id)
    source_bytes, source_filename = _read_source(source_directory, selected_file)

    checksum = hashlib.sha256(source_bytes).hexdigest()
    document_id = uuid5(
        NAMESPACE_URL,
        f"subject={normalized_subject}\ncourse={normalized_course_id}\n"
        f"source={source_filename}\nsha256={checksum}\nparser={PARSER_VERSION}",
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
    parsed, document_title, drafts, embedding_inputs = _prepare(
        source_bytes, source_filename, normalized_subject, normalized_course_id
    )
    page_count, empty_pages = parsed.page_count, parsed.empty_pages

    try:
        vectors = await _embed_all(embedding_provider, embedding_inputs)
    except Exception as error:
        raise IngestionError("embedding provider failed; no records were written") from error
    for vector in vectors:
        if len(vector) != EXPECTED_EMBEDDING_DIMENSIONS:
            raise IngestionError(
                "embedding provider returned "
                f"{len(vector)} dimensions; expected {EXPECTED_EMBEDDING_DIMENSIONS}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise IngestionError("embedding provider returned a non-finite vector value")

    document = Document(
        id=document_id,
        subject=normalized_subject,
        course_id=normalized_course_id,
        title=document_title,
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
            section_title=draft.section_title,
            text=draft.text,
            embedding=vector,
        )
        for draft, vector in zip(drafts, vectors, strict=True)
    ]

    replaced_documents = 0
    try:
        with session_factory.begin() as session:
            current = session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if current is None:
                # A changed file keeps its course and path but gets a new checksum; drop the
                # superseded versions (chunks cascade) so stale content is no longer cited.
                replaced = session.execute(
                    delete(Document).where(
                        Document.course_id == normalized_course_id,
                        Document.source_filename == source_filename,
                        Document.id != document_id,
                    )
                )
                replaced_documents = int(getattr(replaced, "rowcount", 0) or 0)
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
        replaced_documents=replaced_documents,
    )


async def _embed_all(provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    """Embed texts in batches when the provider supports it, else one at a time."""

    embed_batch = getattr(provider, "embed_batch", None)
    if embed_batch is None:
        return [await provider.embed(text) for text in texts]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]
        batch_vectors: list[list[float]] = await embed_batch(batch)
        if len(batch_vectors) != len(batch):
            raise IngestionError(
                f"embedding provider returned {len(batch_vectors)} vectors for {len(batch)} texts"
            )
        vectors.extend(batch_vectors)
    return vectors
