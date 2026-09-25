"""Track the provider that created each document's chunk embeddings."""

import sqlalchemy as sa

from alembic import op

revision = "20260925_0004"
down_revision = "20260924_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("embedding_provider", sa.String(64), nullable=True))
    # Existing rows intentionally remain NULL until explicitly re-embedded.
    op.create_check_constraint(
        "ck_documents_embedding_provider_nonempty",
        "documents",
        "embedding_provider IS NULL OR length(embedding_provider) > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_documents_embedding_provider_nonempty", "documents", type_="check"
    )
    op.drop_column("documents", "embedding_provider")
