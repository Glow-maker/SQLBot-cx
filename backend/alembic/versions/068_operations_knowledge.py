"""Tenant-scoped platform knowledge and credential-free management catalog."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = 'b7e066b1c003'
down_revision = 'b7e066b1c002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('operations_knowledge',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('source_kind', sa.String(16), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('label', sa.String(255), nullable=False),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('embedding', Vector(768)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_operations_knowledge_scope', 'operations_knowledge',
                    ['source_kind', 'tenant_id', 'kind', 'enabled'])
    op.create_table('operations_knowledge_catalog',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('datasource_id', sa.BigInteger(), nullable=False),
        sa.Column('metadata_json', JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'datasource_id', name='uq_operations_knowledge_catalog'))


def downgrade():
    op.drop_table('operations_knowledge_catalog')
    op.drop_index('ix_operations_knowledge_scope', table_name='operations_knowledge')
    op.drop_table('operations_knowledge')
