"""Create course-scoped document, vector chunk, and embedding metadata tables."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "20260924_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.String(length=200), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(course_id) > 0", name="ck_documents_course_id_nonempty"),
        sa.CheckConstraint("length(source_filename) > 0", name="ck_documents_filename_nonempty"),
        sa.CheckConstraint("length(checksum) = 64", name="ck_documents_checksum_sha256_length"),
        sa.CheckConstraint("page_count > 0", name="ck_documents_page_count_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "course_id", name="uq_documents_id_course"),
        sa.UniqueConstraint(
            "course_id", "source_filename", "checksum", name="uq_documents_course_source_checksum"
        ),
    )
    op.create_index("ix_documents_course_id", "documents", ["course_id"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.String(length=200), nullable=False),
        sa.Column("physical_page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(course_id) > 0", name="ck_chunks_course_id_nonempty"),
        sa.CheckConstraint("physical_page_number > 0", name="ck_chunks_page_number_positive"),
        sa.CheckConstraint("chunk_position >= 0", name="ck_chunks_position_nonnegative"),
        sa.CheckConstraint("length(text) > 0", name="ck_chunks_text_nonempty"),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["documents.id", "documents.course_id"],
            name="fk_chunks_document_course",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "course_id", name="uq_chunks_id_course"),
        sa.UniqueConstraint("document_id", "chunk_position", name="uq_chunks_document_position"),
    )
    op.create_index("ix_chunks_course_id", "document_chunks", ["course_id"])
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "embedding_schema_metadata",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_embedding_schema_singleton"),
        sa.CheckConstraint(
            "length(embedding_model) > 0", name="ck_embedding_schema_model_nonempty"
        ),
        sa.CheckConstraint(
            "embedding_dimensions > 0", name="ck_embedding_schema_dimensions_positive"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "embedding_schema_metadata",
            sa.column("id", sa.SmallInteger()),
            sa.column("embedding_model", sa.String()),
            sa.column("embedding_dimensions", sa.Integer()),
        ),
        [{"id": 1, "embedding_model": "gemini-embedding-2", "embedding_dimensions": 768}],
    )


def downgrade() -> None:
    op.drop_table("embedding_schema_metadata")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="document_chunks")
    op.drop_index("ix_chunks_course_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_course_id", table_name="documents")
    op.drop_table("documents")
