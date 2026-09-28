"""Startup checks that runtime embedding settings match the migrated database."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import Settings


class EmbeddingSchemaMismatchError(RuntimeError):
    """Raised when runtime embeddings cannot be stored or queried safely."""


_SCHEMA_METADATA_SQL = text(
    """
    SELECT embedding_model, embedding_dimensions
    FROM embedding_schema_metadata
    WHERE id = 1
    """
)
_VECTOR_TYPE_SQL = text(
    """
    SELECT format_type(attribute.atttypid, attribute.atttypmod)
    FROM pg_attribute AS attribute
    JOIN pg_class AS relation ON relation.oid = attribute.attrelid
    WHERE relation.relnamespace = current_schema()::regnamespace
      AND relation.relname = 'document_chunks'
      AND attribute.attname = 'embedding'
      AND NOT attribute.attisdropped
    """
)
_PROVIDER_MISMATCH_SQL = text(
    "SELECT count(*) FROM documents WHERE embedding_provider IS DISTINCT FROM :provider"
)


def validate_embedding_schema(connection: Connection, settings: Settings) -> None:
    """Reject absent or incompatible migrated model metadata and vector dimensions."""

    metadata = connection.execute(_SCHEMA_METADATA_SQL).mappings().one_or_none()
    if metadata is None:
        raise EmbeddingSchemaMismatchError(
            "embedding schema metadata is missing; apply database migrations before startup"
        )

    expected_model = settings.embedding_model
    expected_dimensions = settings.embedding_dimensions
    actual_model = metadata["embedding_model"]
    actual_dimensions = metadata["embedding_dimensions"]
    if (actual_model, actual_dimensions) != (expected_model, expected_dimensions):
        raise EmbeddingSchemaMismatchError(
            "runtime embedding configuration does not match migrated metadata "
            f"(expected {actual_model}/{actual_dimensions}, configured "
            f"{expected_model}/{expected_dimensions})"
        )

    vector_type = connection.execute(_VECTOR_TYPE_SQL).scalar_one_or_none()
    expected_vector_type = f"vector({expected_dimensions})"
    if vector_type != expected_vector_type:
        raise EmbeddingSchemaMismatchError(
            "document_chunks.embedding column does not match migrated metadata "
            f"(expected {expected_vector_type}, found {vector_type or 'missing'})"
        )
    provider_mismatch_count = connection.execute(
        _PROVIDER_MISMATCH_SQL, {"provider": settings.embedding_provider}
    ).scalar_one()
    if provider_mismatch_count:
        raise EmbeddingSchemaMismatchError(
            f"{provider_mismatch_count} document(s) have unknown or incompatible embedding "
            "providers; re-run ingestion with "
            f"EMBEDDING_PROVIDER={settings.embedding_provider} "
            "before starting retrieval"
        )
