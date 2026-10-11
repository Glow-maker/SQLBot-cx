"""Persisted platform indexing state; deliberately contains no access tokens."""
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class PlatformTableEmbedding(Base):
    __tablename__ = "platform_table_embedding"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(BigInteger, nullable=False)
    datasource_id = Column(BigInteger, nullable=False)
    table_id = Column(BigInteger, nullable=False)
    metadata_json = Column(JSONB, nullable=False)
    metadata_hash = Column(String(64), nullable=False)
    model_version = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("tenant_id", "datasource_id", "table_id", name="uq_platform_embedding_table"),
        Index("ix_platform_embedding_scope", "tenant_id", "datasource_id", "active"),
    )


class PlatformTableEmbeddingChunk(Base):
    __tablename__ = "platform_table_embedding_chunk"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    parent_id = Column(BigInteger, ForeignKey("platform_table_embedding.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(768), nullable=False)
    __table_args__ = (UniqueConstraint("parent_id", "ordinal", name="uq_platform_embedding_chunk"),)


class PlatformEmbeddingSyncJob(Base):
    __tablename__ = "platform_embedding_sync_job"
    id = Column(String(36), primary_key=True)
    tenant_id = Column(BigInteger, nullable=False)
    datasource_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    snapshot = Column(JSONB, nullable=False)
    force = Column(Boolean, nullable=False, default=False)
    counts = Column(JSONB, nullable=False, default=dict)
    errors = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_platform_embedding_active_job", "tenant_id", "datasource_id", unique=True,
              postgresql_where=text("status IN ('pending', 'running')")),
        Index("ix_platform_embedding_latest_job", "tenant_id", "datasource_id", "created_at"),
    )
