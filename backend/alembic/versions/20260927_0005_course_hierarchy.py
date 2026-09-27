"""Add the subject level, document titles, and chunk section titles."""

import sqlalchemy as sa

from alembic import op

revision = "20260927_0005"
down_revision = "20260925_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Documents ingested before subjects existed are grouped under "General" until
    # they are re-ingested from the <subject>/<course>/ folder layout.
    op.add_column(
        "documents",
        sa.Column("subject", sa.String(200), nullable=False, server_default="General"),
    )
    op.alter_column("documents", "subject", server_default=None)
    op.create_check_constraint(
        "ck_documents_subject_nonempty", "documents", "length(subject) > 0"
    )
    op.create_index("ix_documents_subject", "documents", ["subject"])
    op.add_column("documents", sa.Column("title", sa.String(512), nullable=True))
    op.add_column("document_chunks", sa.Column("section_title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "section_title")
    op.drop_column("documents", "title")
    op.drop_index("ix_documents_subject", table_name="documents")
    op.drop_constraint("ck_documents_subject_nonempty", "documents", type_="check")
    op.drop_column("documents", "subject")
