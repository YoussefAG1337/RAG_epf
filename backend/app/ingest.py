"""Command-line entry point for ingesting one course file or a whole folder tree."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from app.config import get_settings
from app.db import create_database_engine, create_session_factory
from app.ingestion import (
    DEFAULT_SUBJECT,
    SUPPORTED_SUFFIXES,
    CourseSelection,
    IngestionError,
    IngestionResult,
    discover_course_files,
    ingest_document,
    preview_document,
)
from app.providers.deterministic import DeterministicEmbeddingProvider
from app.providers.local import LocalEmbeddingProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description=(
            "Ingest course files beneath the configured PDF_SOURCE_DIR, laid out as "
            "<course>/<file> or <subject>/<course>/<file>. Supported formats: "
            + ", ".join(sorted(SUPPORTED_SUFFIXES))
            + "."
        ),
    )
    parser.add_argument(
        "--subject",
        help=(
            "subject that owns the course; with --all, ingest only this subject "
            f"(default for a single file: {DEFAULT_SUBJECT})"
        ),
    )
    parser.add_argument(
        "--course-id",
        help="course that owns the file; with --all, ingest only this course folder",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--file",
        "--pdf",
        dest="file",
        help="file path relative to PDF_SOURCE_DIR",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="ingest every supported file in the course folders",
    )
    parser.add_argument(
        "--provider",
        choices=("local", "deterministic"),
        default=None,
        help="override EMBEDDING_PROVIDER (local: the embeddings container)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and chunk only: print what would be stored, without database or embeddings",
    )
    parser.add_argument(
        "--show-chunks",
        type=int,
        default=3,
        metavar="N",
        help="with --dry-run, print the first N chunks of each file (-1 for all)",
    )
    return parser


def _selections(args: argparse.Namespace, source_directory: Path) -> list[CourseSelection]:
    if not args.all:
        return [
            CourseSelection(
                subject=args.subject or DEFAULT_SUBJECT,
                course_id=args.course_id,
                selected_file=args.file,
            )
        ]
    selections, skipped = discover_course_files(
        source_directory, subject=args.subject, course_id=args.course_id
    )
    for item in skipped:
        print(f"warning: skipped {item.path}: {item.reason}", file=sys.stderr)
    if not selections:
        raise IngestionError(
            "no supported files found; place them in PDF_SOURCE_DIR/<course>/ "
            "or PDF_SOURCE_DIR/<subject>/<course>/ folders"
        )
    return selections


def _dry_run(args: argparse.Namespace) -> int:
    source_directory = get_settings().pdf_source_dir
    selections = _selections(args, source_directory)
    failures = 0
    for selection in selections:
        try:
            preview = preview_document(
                source_directory=source_directory,
                selected_file=selection.selected_file,
                subject=selection.subject,
                course_id=selection.course_id,
            )
        except IngestionError as error:
            failures += 1
            print(f"would fail {selection.selected_file}: {error}", file=sys.stderr)
            continue
        unit = "pages" if preview.paginated else "parts"
        print(
            f"\n== {preview.source_filename}\n"
            f"   subject={selection.subject} course={selection.course_id} "
            f"title={preview.title!r}\n"
            f"   {unit}={preview.page_count} chunks={len(preview.chunks)}"
            + (f" empty={list(preview.empty_pages)}" if preview.empty_pages else "")
        )
        shown = preview.chunks if args.show_chunks < 0 else preview.chunks[: args.show_chunks]
        for page, section, text in shown:
            where = f"p.{page} " if preview.paginated else ""
            snippet = text if len(text) <= 400 else text[:400] + " …"
            print(f"   --- {where}[{section or '-'}]\n      " + snippet.replace("\n", "\n      "))
    print(f"\n{len(selections) - failures} of {len(selections)} files would be ingested")
    return 1 if failures else 0


async def _ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    selected_provider = args.provider or settings.embedding_provider
    engine = create_database_engine(settings)
    provider: DeterministicEmbeddingProvider | LocalEmbeddingProvider = (
        DeterministicEmbeddingProvider(dimensions=settings.embedding_dimensions)
        if selected_provider == "deterministic"
        else LocalEmbeddingProvider(
            settings.embedding_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    )
    try:
        session_factory = create_session_factory(engine)
        selections = _selections(args, settings.pdf_source_dir)
        failures = 0
        for selection in selections:
            try:
                result = await ingest_document(
                    source_directory=settings.pdf_source_dir,
                    selected_file=selection.selected_file,
                    subject=selection.subject,
                    course_id=selection.course_id,
                    session_factory=session_factory,
                    embedding_provider=provider,
                )
            except IngestionError as error:
                if not args.all:
                    raise
                failures += 1
                print(
                    f"ingestion failed for {selection.selected_file}: {error}", file=sys.stderr
                )
                continue
            _report(result, selection)
    finally:
        if isinstance(provider, LocalEmbeddingProvider):
            await provider.aclose()
        engine.dispose()

    if args.all:
        print(f"ingested {len(selections) - failures} of {len(selections)} files")
    return 1 if failures else 0


def _report(result: IngestionResult, selection: CourseSelection) -> None:
    for page_number in result.empty_pages:
        print(
            f"warning: {result.source_filename} page {page_number} "
            "contains no extractable text",
            file=sys.stderr,
        )
    replaced = (
        f" replaced={result.replaced_documents}" if result.replaced_documents else ""
    )
    print(
        f"ingested {result.source_filename}: subject={selection.subject.strip()} "
        f"course={selection.course_id.strip()} "
        f"pages={result.page_count} chunks={result.chunk_count}{replaced} "
        f"document_id={result.document_id} sha256={result.checksum}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and return a shell-friendly exit code."""

    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    if args.file is not None and args.course_id is None:
        parser.error("--course-id is required with --file")
    try:
        if args.dry_run:
            return _dry_run(args)
        return asyncio.run(_ingest(args))
    except IngestionError as error:
        print(f"ingestion failed: {error}", file=sys.stderr)
        return 1
    except ValidationError:
        print(
            "ingestion failed: invalid runtime configuration; check .env "
            "(EMBEDDING_PROVIDER, ANSWER_PROVIDER, GEMINI_API_KEY)",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        # The type name helps diagnose (e.g. OperationalError: database not ready) without
        # printing messages that could contain credentials.
        print(
            f"ingestion failed ({type(error).__name__}); no credentials or provider details "
            "were written to output",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
