"""SQLBot feedback and operations snapshots (independent of chat retention)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b7e066b1c002'
down_revision = 'a6e066b1c001'
branch_labels = None
depends_on = None


def col(name, type_, nullable=False):
    return sa.Column(name, type_, nullable=nullable)


def pk():
    return sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True)


def identity_columns():
    return [col('scope', sa.String(20)), col('auth_source', sa.String(20)),
            col('tenant_id', sa.BigInteger()), col('user_id', sa.BigInteger()),
            col('account', sa.String(100)), col('user_name', sa.String(100))]


def upgrade():
    op.create_table('ops_interaction', pk(), *identity_columns(),
        col('chat_id', sa.BigInteger(), True), col('record_id', sa.BigInteger(), True),
        col('root_question_id', sa.BigInteger(), True), col('kind', sa.String(20)),
        col('question', sa.Text()), col('sql', sa.Text(), True),
        col('status', sa.String(30)), col('outcome', sa.String(50), True),
        col('error_summary', sa.String(1500), True), col('error_class', sa.String(100), True),
        col('created_at', sa.DateTime()), col('finished_at', sa.DateTime(), True), col('expires_at', sa.DateTime()),
        col('duration_ms', sa.Float(), True), col('first_output_ms', sa.Float(), True),
        col('total_tokens', sa.Integer(), True), col('round_count', sa.Integer()), col('retry_count', sa.Integer()),
        col('datasource_id', sa.BigInteger(), True), col('stages_json', postgresql.JSONB()),
        col('knowledge_json', postgresql.JSONB()))
    op.create_table('ops_feedback', pk(), *identity_columns(),
        col('interaction_id', sa.BigInteger(), True), col('record_id', sa.BigInteger(), True),
        col('submission_id', sa.String(36)), col('type', sa.String(40)), col('content', sa.String(4000)),
        col('expected', sa.String(2000)), col('status', sa.String(20)), col('reply', sa.String(2000)),
        col('replied_by', sa.BigInteger(), True), col('replied_at', sa.DateTime(), True),
        col('created_at', sa.DateTime()), col('expires_at', sa.DateTime()),
        sa.UniqueConstraint('auth_source', 'tenant_id', 'user_id', 'submission_id', name='uq_ops_feedback_submission'))
    op.create_table('ops_report', pk(), col('scope', sa.String(20)), col('tenant_id', sa.BigInteger()),
        col('auth_source', sa.String(20)), col('created_by', sa.BigInteger()),
        col('created_at', sa.DateTime()), col('expires_at', sa.DateTime()), col('status', sa.String(20)),
        col('range_start', sa.DateTime()), col('range_end', sa.DateTime()),
        col('started_at', sa.DateTime(), True), col('finished_at', sa.DateTime(), True), col('run_token', sa.String(36)),
        *[col(name, postgresql.JSONB()) for name in ('config_json', 'stats_json', 'sample_json', 'result_json', 'progress_json')])
    op.create_table('ops_grant', pk(), col('tenant_id', sa.BigInteger()), col('user_id', sa.BigInteger()),
        col('account', sa.String(100)), col('enabled', sa.Boolean()), col('updated_by', sa.BigInteger()),
        col('updated_at', sa.DateTime()), sa.UniqueConstraint('tenant_id', 'user_id', name='uq_ops_grant_identity'))
    for table in ('ops_interaction', 'ops_feedback', 'ops_report'):
        op.create_index(f'ix_{table}_tenant_created', table, ['scope', 'tenant_id', 'created_at'])
        op.create_index(f'ix_{table}_expires', table, ['expires_at'])
    op.create_index('ix_ops_interaction_record', 'ops_interaction', ['record_id'])
    op.create_index('ix_ops_interaction_chat', 'ops_interaction', ['scope', 'tenant_id', 'user_id', 'chat_id'])


def downgrade():
    # Operational records have their own retention policy; rollback image instead
    # of deleting feedback. Explicit schema downgrade is intentionally destructive.
    for table in ('ops_grant', 'ops_report', 'ops_feedback', 'ops_interaction'):
        op.drop_table(table)
