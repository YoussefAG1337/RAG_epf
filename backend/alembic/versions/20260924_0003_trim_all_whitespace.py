"""Reject chunk text containing only whitespace characters."""

from alembic import op

revision = "20260924_0003"
down_revision = "20260924_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_chunks_text_nonempty", "document_chunks", type_="check")
    op.create_check_constraint(
        "ck_chunks_text_nonempty", "document_chunks", "text ~ '[^[:space:]]'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_chunks_text_nonempty", "document_chunks", type_="check")
    op.create_check_constraint(
        "ck_chunks_text_nonempty", "document_chunks", "length(btrim(text)) > 0"
    )
