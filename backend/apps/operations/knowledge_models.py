"""Platform knowledge is separate from local CoreDatasource identities."""
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()
json_type = JSON().with_variant(JSONB(), 'postgresql')


def utcnow():
    return datetime.now(timezone.utc)


class KnowledgeEntry(Base):
    __tablename__ = 'operations_knowledge'
    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    source_kind = Column(String(16), nullable=False, default='platform')
    tenant_id = Column(BigInteger, nullable=False)
    kind = Column(String(32), nullable=False)
    label = Column(String(255), nullable=False)
    payload = Column(json_type, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    embedding = Column(Vector(768), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (Index('ix_operations_knowledge_scope', 'source_kind', 'tenant_id', 'kind', 'enabled'),)


class KnowledgeCatalog(Base):
    __tablename__ = 'operations_knowledge_catalog'
    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    tenant_id = Column(BigInteger, nullable=False)
    datasource_id = Column(BigInteger, nullable=False)
    metadata_json = Column(json_type, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint('tenant_id', 'datasource_id', name='uq_operations_knowledge_catalog'),)
