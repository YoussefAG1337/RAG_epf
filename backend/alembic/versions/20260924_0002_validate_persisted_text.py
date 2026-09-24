"""Strengthen persisted checksum and chunk-text validation."""

from alembic import op

revision = "20260924_0002"
down_revision = "20260924_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_documents_checksum_sha256_length", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_checksum_sha256_hex",
        "documents",
        "checksum ~ '^[0-9a-fA-F]{64}$'",
    )
    op.drop_constraint("ck_chunks_text_nonempty", "document_chunks", type_="check")
    op.create_check_constraint(
        "ck_chunks_text_nonempty", "document_chunks", "text ~ '[^[:space:]]'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_chunks_text_nonempty", "document_chunks", type_="check")
    op.create_check_constraint(
        "ck_chunks_text_nonempty", "document_chunks", "length(text) > 0"
    )
    op.drop_constraint("ck_documents_checksum_sha256_hex", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_checksum_sha256_length", "documents", "length(checksum) = 64"
    )
