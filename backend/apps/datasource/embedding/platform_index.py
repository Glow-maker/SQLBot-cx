"""Durable single-worker indexing and exact, scope-filtered table recall."""
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.datasource.embedding.platform_models import PlatformEmbeddingSyncJob as Job
from apps.datasource.embedding.platform_models import PlatformTableEmbedding as Table
from apps.datasource.embedding.platform_models import PlatformTableEmbeddingChunk as Chunk, utcnow
from apps.datasource.embedding.platform_text import MetadataSnapshot, canonical_table, metadata_hash

logger = logging.getLogger(__name__)
ACTIVE = ("pending", "running")


def job_view(job):
    if job is None:
        return None
    return dict(jobId=job.id, datasourceId=job.datasource_id, status=job.status, **job.counts,
                errors=job.errors, createdAt=job.created_at, startedAt=job.started_at, finishedAt=job.finished_at)


class PlatformIndex:
    def __init__(self, engine, encoder_factory):
        self.engine = engine
        self.encoder_factory = encoder_factory
        self._encoder = None
        self._encoder_lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._queued = set()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="platform-index")

    def encoder(self):
        with self._encoder_lock:
            if self._encoder is None:
                started = time.perf_counter()
                self._encoder = self.encoder_factory()
                logger.info("Platform encoder_load_ms=%.1f", (time.perf_counter()-started)*1000)
            return self._encoder

    def get(self, tenant_id, job_id=None, datasource_id=None):
        with Session(self.engine) as session:
            query = select(Job).where(Job.tenant_id == tenant_id)
            if job_id is not None:
                query = query.where(Job.id == job_id)
            if datasource_id is not None:
                query = query.where(Job.datasource_id == datasource_id)
            return job_view(session.scalar(query.order_by(Job.created_at.desc()).limit(1)))

    def submit(self, tenant_id, user_id, snapshot, force=False):
        # Re-validate and whitelist even when invoked outside the HTTP route.
        snapshot = MetadataSnapshot.model_validate(snapshot).model_dump()
        if snapshot["tenantId"] != tenant_id:
            raise ValueError("Tenant mismatch")
        scope = (Job.tenant_id == tenant_id, Job.datasource_id == snapshot["datasourceId"], Job.status.in_(ACTIVE))
        with Session(self.engine) as session:
            job = session.scalar(select(Job).where(*scope))
            if job is None:
                job = Job(id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
                          datasource_id=snapshot["datasourceId"], snapshot=snapshot, force=force,
                          counts=dict(total=len(snapshot["tableIds"]), processed=0, added=0, updated=0,
                                      skipped=0, invalidated=0, failed=0), errors=[])
                session.add(job)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    job = session.scalar(select(Job).where(*scope))
                    if job is None:
                        raise
            result = job_view(job)
        self.enqueue(result["jobId"])
        return result

    def enqueue(self, job_id):
        with self._queue_lock:
            if job_id in self._queued:
                return
            self._queued.add(job_id)
        try:
            self.executor.submit(self._run_queued, job_id)
        except Exception:
            with self._queue_lock:
                self._queued.discard(job_id)
            raise

    def _run_queued(self, job_id):
        try:
            self.run(job_id)
        finally:
            with self._queue_lock:
                self._queued.discard(job_id)

    def recover(self):
        with Session(self.engine) as session, session.begin():
            session.execute(update(Job).where(Job.status == "running").values(
                status="failed", finished_at=utcnow(), errors=[{"code": "PROCESS_INTERRUPTED"}]))
            pending = list(session.scalars(select(Job.id).where(Job.status == "pending").order_by(Job.created_at)))
        for job_id in pending:
            self.enqueue(job_id)

    def close(self):
        self.executor.shutdown(wait=True)

    def _progress(self, job_id, counts, errors, status=None):
        values = dict(counts=dict(counts), errors=list(errors[:20]))
        if status:
            values.update(status=status, finished_at=utcnow(), snapshot={})
        with Session(self.engine) as session, session.begin():
            session.execute(update(Job).where(Job.id == job_id).values(**values))

    def run(self, job_id):
        with Session(self.engine) as session, session.begin():
            claimed = session.execute(update(Job).where(Job.id == job_id, Job.status == "pending").values(
                status="running", started_at=utcnow())).rowcount
            if not claimed:
                return
            job = session.get(Job, job_id)
            snapshot, tenant, ds, force = job.snapshot, job.tenant_id, job.datasource_id, job.force
            counts, errors = dict(job.counts), []
        try:
            encoder = self.encoder() if snapshot["tables"] else None
            for failure in snapshot["failures"]:
                counts["failed"] += 1
                counts["processed"] += 1
                errors.append(dict(tableId=failure["tableId"], code="METADATA_READ_FAILED"))
            for table in snapshot["tables"]:
                try:
                    metadata = canonical_table(snapshot, table)
                    digest = metadata_hash(metadata, encoder.version)
                    with Session(self.engine) as session:
                        old = session.scalar(select(Table).where(Table.tenant_id == tenant,
                            Table.datasource_id == ds, Table.table_id == table["tableId"]))
                        unchanged = old is not None and old.metadata_hash == digest and old.model_version == encoder.version
                    if unchanged and not force:
                        with Session(self.engine) as session, session.begin():
                            session.execute(update(Table).where(Table.tenant_id == tenant,
                                Table.datasource_id == ds, Table.table_id == table["tableId"]).values(active=True, synced_at=utcnow()))
                        counts["skipped"] += 1
                    else:
                        chunks = encoder.chunks(metadata)
                        # Per-table batches isolate failures and never publish a partial table.
                        vectors = encoder.documents(chunks)
                        if not chunks or len(chunks) != len(vectors):
                            raise ValueError("Incomplete table embedding")
                        with Session(self.engine) as session, session.begin():
                            row = session.scalar(select(Table).where(Table.tenant_id == tenant,
                                Table.datasource_id == ds, Table.table_id == table["tableId"]))
                            added = row is None
                            if added:
                                row = Table(tenant_id=tenant, datasource_id=ds, table_id=table["tableId"])
                                session.add(row)
                            row.metadata_json, row.metadata_hash = metadata, digest
                            row.model_version, row.active = encoder.version, True
                            row.updated_at = row.synced_at = utcnow()
                            session.flush()
                            session.execute(delete(Chunk).where(Chunk.parent_id == row.id))
                            session.add_all([Chunk(parent_id=row.id, ordinal=i, content=c, embedding=v)
                                             for i, (c, v) in enumerate(zip(chunks, vectors))])
                        counts["added" if added else "updated"] += 1
                except Exception as exc:
                    counts["failed"] += 1
                    errors.append(dict(tableId=table["tableId"], code="TABLE_EMBEDDING_FAILED"))
                    logger.warning("Index job %s table %s failed (%s)", job_id, table["tableId"], type(exc).__name__)
                counts["processed"] += 1
                self._progress(job_id, counts, errors)
            with Session(self.engine) as session, session.begin():
                # Snapshot validation guarantees this is an authoritative inventory, not a partial response.
                result = session.execute(update(Table).where(Table.tenant_id == tenant, Table.datasource_id == ds,
                    Table.active.is_(True), Table.table_id.not_in(snapshot["tableIds"])).values(active=False, updated_at=utcnow()))
                counts["invalidated"] = result.rowcount
            status = "succeeded"
            if counts["failed"]:
                status = "partial_failed" if counts["processed"] > counts["failed"] else "failed"
            self._progress(job_id, counts, errors, status)
        except Exception as exc:
            logger.warning("Index job %s failed (%s)", job_id, type(exc).__name__)
            self._progress(job_id, counts, errors + [{"code": "INDEX_JOB_FAILED"}], "failed")

    def recall(self, tenant_id, question, entries, limit=50):
        allowed = sorted({(int(e["datasourceId"]), int(e["tableId"])) for e in entries if e.get("tableId") is not None})
        if not allowed or not question.strip():
            return [], False
        encoder = self.encoder()
        start = time.perf_counter()
        with Session(self.engine) as session:
            filters = (Table.tenant_id == tenant_id, Table.active.is_(True), Table.model_version == encoder.version,
                       tuple_(Table.datasource_id, Table.table_id).in_(allowed))
            if session.scalar(select(Table.id).where(*filters).limit(1)) is None:
                return [], False
            vector = encoder.query(question)
            encoded_at = time.perf_counter()
            distance = func.min(Chunk.embedding.cosine_distance(vector))
            rows = session.execute(select(Table.datasource_id, Table.table_id, distance.label("distance"))
                .join(Chunk, Chunk.parent_id == Table.id).where(*filters)
                .group_by(Table.id).order_by(distance, Table.datasource_id, Table.table_id).limit(min(limit, 50))).all()
        logger.info("Platform recall query_embedding_ms=%.1f vector_search_ms=%.1f candidates=%s",
                    (encoded_at-start)*1000, (time.perf_counter()-encoded_at)*1000, len(rows))
        by_id = {(int(e["datasourceId"]), int(e["tableId"])): e for e in entries if e.get("tableId") is not None}
        return [by_id[(ds, table)] for ds, table, _ in rows], True

    def enrich(self, tenant_id, entries):
        allowed = sorted({(int(e["datasourceId"]), int(e["tableId"])) for e in entries if e.get("tableId") is not None})
        if not allowed:
            return entries
        with Session(self.engine) as session:
            rows = session.scalars(select(Table).where(Table.tenant_id == tenant_id, Table.active.is_(True),
                tuple_(Table.datasource_id, Table.table_id).in_(allowed)))
            metadata = {(r.datasource_id, r.table_id): r.metadata_json for r in rows}
        result = []
        for entry in entries:
            key = (int(entry["datasourceId"]), int(entry["tableId"])) if entry.get("tableId") is not None else None
            indexed = metadata.get(key, {})
            # Current catalog is authoritative. A stale identity or field must not
            # be restored from an older administrator-owned index snapshot.
            if indexed.get("name") != entry.get("name") or indexed.get("schemaName", "") != entry.get("schemaName", ""):
                indexed = {}
            indexed = dict(indexed)
            fields = {f["name"] for f in entry.get("fields", [])}
            indexed["fields"] = [f for f in indexed.get("fields", []) if f["name"] in fields]
            indexed["relations"] = [r for r in indexed.get("relations", [])
                if (key[0], r.get("sourceTableId")) in allowed
                and (key[0], r.get("targetTableId")) in allowed] if key else []
            result.append(dict(entry, _indexed_metadata=indexed))
        return result


_instance = None


def get_platform_index():
    global _instance
    if _instance is None:
        from common.core.db import engine
        from apps.ai_model.embedding import EmbeddingModelCache, local_embedding_model
        from apps.datasource.embedding.platform_text import MetadataEncoder
        _instance = PlatformIndex(engine, lambda: MetadataEncoder(EmbeddingModelCache.get_model(), local_embedding_model.name))
    return _instance
