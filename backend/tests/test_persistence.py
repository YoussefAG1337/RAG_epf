"""Tests for persistence metadata and startup embedding-schema compatibility."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.config import Settings
from app.models import Base
from app.schema_guard import EmbeddingSchemaMismatchError, validate_embedding_schema


class FakeResult:
    def __init__(self, row: dict[str, Any] | None = None, scalar: str | None = None) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self) -> "FakeResult":
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row

    def scalar_one_or_none(self) -> str | None:
        return self._scalar


class FakeConnection:
    def __init__(self, model: str | None, dimensions: int | None, vector_type: str | None) -> None:
        self.model = model
        self.dimensions = dimensions
        self.vector_type = vector_type

    def execute(self, statement: Any) -> FakeResult:
        if "embedding_schema_metadata" in str(statement):
            if self.model is None or self.dimensions is None:
                return FakeResult()
            return FakeResult(
                {"embedding_model": self.model, "embedding_dimensions": self.dimensions}
            )
        return FakeResult(scalar=self.vector_type)


def test_models_include_course_scope_provenance_and_vector_contract() -> None:
    documents = Base.metadata.tables["documents"]
    chunks = Base.metadata.tables["document_chunks"]

    assert {"course_id", "source_filename", "checksum", "page_count"} <= set(
        documents.columns.keys()
    )
    assert {
        "course_id",
        "physical_page_number",
        "chunk_position",
        "text",
        "embedding",
    } <= set(chunks.columns.keys())
    embedding_type = chunks.c.embedding.type
    assert isinstance(embedding_type, Vector)
    assert embedding_type.dim == 768
    assert any(isinstance(item, CheckConstraint) for item in chunks.constraints)
    assert any(
        isinstance(item, UniqueConstraint)
        and "chunk_position" in {column.name for column in item.columns}
        for item in chunks.constraints
    )
    assert {index.name for index in chunks.indexes} == {
        "ix_chunks_course_id",
        "ix_chunks_embedding_hnsw",
    }


def test_startup_guard_accepts_matching_model_metadata_and_vector_column() -> None:
    settings = Settings()

    validate_embedding_schema(
        FakeConnection("gemini-embedding-2", 768, "vector(768)"),  # type: ignore[arg-type]
        settings,
    )


@pytest.mark.parametrize(
    ("model", "dimensions", "vector_type", "message"),
    [
        (None, None, None, "metadata is missing"),
        ("gemini-embedding-2", 384, "vector(384)", "does not match migrated metadata"),
        ("gemini-embedding-2", 768, "vector(384)", "column does not match"),
    ],
)
def test_startup_guard_rejects_missing_or_incompatible_database_schema(
    model: str | None,
    dimensions: int | None,
    vector_type: str | None,
    message: str,
) -> None:
    with pytest.raises(EmbeddingSchemaMismatchError, match=message):
        validate_embedding_schema(
            FakeConnection(model, dimensions, vector_type),  # type: ignore[arg-type]
            Settings(),
        )


def test_alembic_offline_migration_contains_required_schema_ddl() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head", "--sql"],
        cwd=backend_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    migration_sql = result.stdout.lower()

    assert "create extension if not exists vector" in migration_sql
    assert "create table documents" in migration_sql
    assert "create table document_chunks" in migration_sql
    assert "vector(768)" in migration_sql
    assert "ix_chunks_embedding_hnsw" in migration_sql
    assert "uq_chunks_document_position" in migration_sql
    assert "gemini-embedding-2" in migration_sql
