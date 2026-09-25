"""Course-scoped persistence models for source documents and vector chunks."""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by application models and Alembic."""


class Document(Base):
    """A source PDF identity and the metadata needed to trace its chunks."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(course_id) > 0", name="ck_documents_course_id_nonempty"),
        CheckConstraint("length(source_filename) > 0", name="ck_documents_filename_nonempty"),
        CheckConstraint("checksum ~ '^[0-9a-fA-F]{64}$'", name="ck_documents_checksum_sha256_hex"),
        CheckConstraint("page_count > 0", name="ck_documents_page_count_positive"),
        CheckConstraint(
            "embedding_provider IS NULL OR length(embedding_provider) > 0",
            name="ck_documents_embedding_provider_nonempty",
        ),
        UniqueConstraint("id", "course_id", name="uq_documents_id_course"),
        UniqueConstraint(
            "course_id", "source_filename", "checksum", name="uq_documents_course_source_checksum"
        ),
        Index("ix_documents_course_id", "course_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    course_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentChunk(Base):
    """An embedded text span retaining its course, document, and page provenance."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("length(course_id) > 0", name="ck_chunks_course_id_nonempty"),
        CheckConstraint("physical_page_number > 0", name="ck_chunks_page_number_positive"),
        CheckConstraint("chunk_position >= 0", name="ck_chunks_position_nonnegative"),
        CheckConstraint("text ~ '[^[:space:]]'", name="ck_chunks_text_nonempty"),
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["documents.id", "documents.course_id"],
            name="fk_chunks_document_course",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", name="uq_chunks_id_course"),
        UniqueConstraint("document_id", "chunk_position", name="uq_chunks_document_position"),
        Index("ix_chunks_course_id", "course_id"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    course_id: Mapped[str] = mapped_column(String(200), nullable=False)
    physical_page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmbeddingSchemaMetadata(Base):
    """The embedding pair for which the persisted vector column was migrated."""

    __tablename__ = "embedding_schema_metadata"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_embedding_schema_singleton"),
        CheckConstraint("length(embedding_model) > 0", name="ck_embedding_schema_model_nonempty"),
        CheckConstraint("embedding_dimensions > 0", name="ck_embedding_schema_dimensions_positive"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
