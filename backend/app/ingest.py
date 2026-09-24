"""Command-line entry point for ingesting one course PDF."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.config import get_settings
from app.db import create_database_engine, create_session_factory
from app.ingestion import IngestionError, ingest_pdf
from app.providers.deterministic import DeterministicEmbeddingProvider
from app.providers.gemini import GeminiEmbeddingProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Ingest one selected PDF beneath the configured PDF_SOURCE_DIR.",
    )
    parser.add_argument("--course-id", required=True, help="course that owns the ingested PDF")
    parser.add_argument(
        "--pdf", required=True, help="PDF filename or relative path beneath PDF_SOURCE_DIR"
    )
    parser.add_argument(
        "--provider",
        choices=("deterministic", "gemini"),
        default="deterministic",
        help="embedding provider (default: deterministic; Gemini requires GEMINI_API_KEY)",
    )
    return parser


async def _ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_database_engine(settings)
    provider = (
        DeterministicEmbeddingProvider(dimensions=settings.embedding_dimensions)
        if args.provider == "deterministic"
        else GeminiEmbeddingProvider(
            settings.gemini_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    )
    try:
        result = await ingest_pdf(
            source_directory=settings.pdf_source_dir,
            selected_pdf=args.pdf,
            course_id=args.course_id,
            session_factory=create_session_factory(engine),
            embedding_provider=provider,
        )
    finally:
        if isinstance(provider, GeminiEmbeddingProvider):
            provider.close()
        engine.dispose()

    for page_number in result.empty_pages:
        print(f"warning: physical page {page_number} contains no extractable text", file=sys.stderr)
    print(
        f"ingested {result.source_filename}: course={args.course_id.strip()} "
        f"pages={result.page_count} chunks={result.chunk_count} "
        f"document_id={result.document_id} sha256={result.checksum}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and return a shell-friendly exit code."""

    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_ingest(args))
    except IngestionError as error:
        print(f"ingestion failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "ingestion failed; no credentials or provider details were written to output",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
