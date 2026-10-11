"""Platform metadata index and durable manual sync queue.

Revision ID: a6e066b1c001
Revises: 8ff90df7871d
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = "a6e066b1c001"
down_revision = "8ff90df7871d"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("platform_table_embedding",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=False),
        sa.Column("metadata_hash", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "datasource_id", "table_id", name="uq_platform_embedding_table"))
    op.create_index("ix_platform_embedding_scope", "platform_table_embedding", ["tenant_id", "datasource_id", "active"])
    op.create_table("platform_table_embedding_chunk",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("platform_table_embedding.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.UniqueConstraint("parent_id", "ordinal", name="uq_platform_embedding_chunk"))
    op.create_table("platform_embedding_sync_job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("force", sa.Boolean(), nullable=False),
        sa.Column("counts", JSONB(), nullable=False),
        sa.Column("errors", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("uq_platform_embedding_active_job", "platform_embedding_sync_job", ["tenant_id", "datasource_id"],
                    unique=True, postgresql_where=sa.text("status IN ('pending', 'running')"))
    op.create_index("ix_platform_embedding_latest_job", "platform_embedding_sync_job", ["tenant_id", "datasource_id", "created_at"])


def downgrade():
    op.drop_table("platform_embedding_sync_job")
    op.drop_table("platform_table_embedding_chunk")
    op.drop_table("platform_table_embedding")
