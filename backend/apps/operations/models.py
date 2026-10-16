"""SQLBot-owned operational snapshots. No FK to deletable chat history."""
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Column, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def now_utc():
    return datetime.utcnow()


def expiry():
    return now_utc() + timedelta(days=180)


def pk():
    from sqlalchemy import Integer
    return Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)


def json_column():
    return Column(JSON().with_variant(JSONB, 'postgresql'), nullable=False)


class Interaction(SQLModel, table=True):
    __tablename__ = 'ops_interaction'
    id: int | None = Field(default=None, sa_column=pk())
    scope: str = Field(max_length=20, index=True)
    auth_source: str = Field(max_length=20)
    tenant_id: int = Field(index=True)
    user_id: int = Field(index=True)
    account: str = Field(default='', max_length=100)
    user_name: str = Field(default='', max_length=100)
    chat_id: int | None = Field(default=None, index=True)
    record_id: int | None = Field(default=None, index=True)
    root_question_id: int | None = Field(default=None, index=True)
    kind: str = Field(default='question', max_length=20)
    question: str = Field(default='', sa_column=Column(Text, nullable=False))
    sql: str | None = Field(default=None, sa_column=Column(Text))
    status: str = Field(default='running', max_length=30, index=True)
    outcome: str | None = Field(default=None, max_length=50)
    error_summary: str | None = Field(default=None, max_length=1500)
    error_class: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=now_utc, index=True)
    finished_at: datetime | None = None
    expires_at: datetime = Field(default_factory=expiry, index=True)
    duration_ms: float | None = None
    first_output_ms: float | None = None
    total_tokens: int | None = None
    round_count: int = 1
    retry_count: int = 0
    datasource_id: int | None = Field(default=None, index=True)
    stages_json: list = Field(default_factory=list, sa_column=json_column())
    knowledge_json: list = Field(default_factory=list, sa_column=json_column())


class Feedback(SQLModel, table=True):
    __tablename__ = 'ops_feedback'
    __table_args__ = (UniqueConstraint('auth_source', 'tenant_id', 'user_id', 'submission_id', name='uq_ops_feedback_submission'),)
    id: int | None = Field(default=None, sa_column=pk())
    scope: str = Field(max_length=20, index=True)
    auth_source: str = Field(max_length=20)
    tenant_id: int = Field(index=True)
    user_id: int = Field(index=True)
    account: str = Field(default='', max_length=100)
    user_name: str = Field(default='', max_length=100)
    interaction_id: int | None = Field(default=None, index=True)
    record_id: int | None = None
    submission_id: str = Field(max_length=36)
    type: str = Field(max_length=40)
    content: str = Field(max_length=4000)
    expected: str = Field(default='', max_length=2000)
    status: str = Field(default='pending', max_length=20, index=True)
    reply: str = Field(default='', max_length=2000)
    replied_by: int | None = None
    replied_at: datetime | None = None
    created_at: datetime = Field(default_factory=now_utc, index=True)
    expires_at: datetime = Field(default_factory=expiry, index=True)


class ReportTask(SQLModel, table=True):
    __tablename__ = 'ops_report'
    id: int | None = Field(default=None, sa_column=pk())
    scope: str = Field(max_length=20, index=True)
    tenant_id: int = Field(index=True)
    auth_source: str = Field(max_length=20)
    created_by: int
    created_at: datetime = Field(default_factory=now_utc, index=True)
    expires_at: datetime = Field(default_factory=expiry, index=True)
    status: str = Field(default='pending', max_length=20, index=True)
    range_start: datetime
    range_end: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    run_token: str = Field(default='', max_length=36)
    config_json: dict = Field(default_factory=dict, sa_column=json_column())
    stats_json: dict = Field(default_factory=dict, sa_column=json_column())
    sample_json: list = Field(default_factory=list, sa_column=json_column())
    result_json: dict = Field(default_factory=dict, sa_column=json_column())
    progress_json: dict = Field(default_factory=dict, sa_column=json_column())


class OperationsGrant(SQLModel, table=True):
    __tablename__ = 'ops_grant'
    __table_args__ = (UniqueConstraint('tenant_id', 'user_id', name='uq_ops_grant_identity'),)
    id: int | None = Field(default=None, sa_column=pk())
    tenant_id: int = Field(index=True)
    user_id: int
    account: str = Field(default='', max_length=100)
    enabled: bool = True
    updated_by: int
    updated_at: datetime = Field(default_factory=now_utc)
