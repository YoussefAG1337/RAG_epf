"""Add full-text keyword search on chunks, for hybrid (keyword + vector) retrieval.

The search vector is a generated column, so existing chunks get one without re-ingestion
and new chunks get one automatically. Section titles weigh more than body text.
"""

from alembic import op

revision = "20261028_0007"
down_revision = "20260928_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('french', coalesce(section_title, '')), 'A')
            || setweight(to_tsvector('french', text), 'B')
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_chunks_search_vector ON document_chunks USING gin (search_vector)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_chunks_search_vector")
    op.execute("ALTER TABLE document_chunks DROP COLUMN search_vector")
