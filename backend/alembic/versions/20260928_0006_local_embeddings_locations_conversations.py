"""Switch to local 1024-dim embeddings, store chunk locations, add saved conversations.

Existing documents and chunks are deleted: their 768-dim Gemini vectors cannot be converted,
and they lack source locations. Course files are untouched; re-run ingestion to rebuild.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260928_0006"
down_revision = "20260927_0005"
branch_labels = None
depends_on = None

MODEL = "Qwen/Qwen3-Embedding-0.6B"
DIMENSIONS = 1024


def upgrade() -> None:
    op.execute("DELETE FROM document_chunks")
    op.execute("DELETE FROM documents")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="document_chunks")
    op.execute(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({DIMENSIONS})")
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.execute(
        f"UPDATE embedding_schema_metadata SET embedding_model = '{MODEL}', "
        f"embedding_dimensions = {DIMENSIONS}, updated_at = now() WHERE id = 1"
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "locations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(title) > 0", name="ck_conversations_title_nonempty"),
    )
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "conversation_id", "position", name="uq_messages_conversation_position"
        ),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_index("ix_conversations_updated_at", table_name="conversations")
    op.drop_table("conversations")
    op.drop_column("document_chunks", "locations")
    op.execute("DELETE FROM document_chunks")
    op.execute("DELETE FROM documents")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="document_chunks")
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(768)")
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.execute(
        "UPDATE embedding_schema_metadata SET embedding_model = 'gemini-embedding-2', "
        "embedding_dimensions = 768, updated_at = now() WHERE id = 1"
    )
