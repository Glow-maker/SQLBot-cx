"""Run with unittest against the dedicated sqlbot_index_test PostgreSQL database."""
import ast
import importlib.util
import os
import threading
import time
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text, func
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError

from apps.datasource.embedding.platform_models import Base, PlatformTableEmbedding as Table, PlatformTableEmbeddingChunk as Chunk, PlatformEmbeddingSyncJob as Job
from apps.datasource.embedding.platform_index import PlatformIndex
from apps.datasource.embedding.platform_text import MetadataSnapshot, MetadataEncoder, canonical_table, metadata_hash, normalize_vector
from apps.datasource.embedding.platform_recall import fuse, routing_card


def snapshot(count=1, tenant=1, ds=3):
    return dict(tenantId=tenant, datasourceId=ds, datasourceName="仓库", datasourceType="pg", complete=True,
        tableIds=list(range(1, count + 1)), failures=[], tables=[dict(tableId=i, name=f"orders_{i}", schemaName="sales",
        comment="订单", fields=[dict(fieldId=i*100+j, name=f"field_{j}", alias="尾部指标" if j==25 else "",
                                    type="int", comment="说明") for j in range(26)]) for i in range(1, count+1)])


class FakeEncoder:
    version = "model-v1"
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.query_calls = 0
    def chunks(self, metadata):
        return [metadata["name"] + " " + str(metadata["fields"])]
    def documents(self, chunks):
        self.calls += len(chunks)
        if self.fail:
            raise ValueError("password=must-not-appear-in-job")
        return [[1.0] + [0.0]*767 for _ in chunks]
    def query(self, question):
        self.query_calls += 1
        return [1.0] + [0.0]*767


class ContractTests(unittest.TestCase):
    def test_shared_model_and_tokenizer_are_serialized(self):
        from apps.ai_model.embedding import SynchronizedEmbeddings
        busy = threading.Lock()

        def guarded(value, **kwargs):
            if not busy.acquire(blocking=False):
                raise RuntimeError("Already borrowed")
            try:
                time.sleep(0.002)
                return [1.0] + [0.0] * 767
            finally:
                busy.release()

        raw = SimpleNamespace(embed_query=guarded,
            embed_documents=lambda texts: [guarded(t) for t in texts],
            _client=SimpleNamespace(tokenizer=SimpleNamespace(encode=guarded)))
        model = SynchronizedEmbeddings(raw)
        encoder = MetadataEncoder.__new__(MetadataEncoder)
        encoder.inference_lock = model.inference_lock
        encoder.tokenizer = model._client.tokenizer
        calls = [lambda: model.embed_query("question"), lambda: model.embed_documents(["table"]),
                 lambda: encoder._token_length("metadata")]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda i: calls[i % 3](), range(30)))

    def test_snapshot_strips_credentials(self):
        value = snapshot()
        value["password"] = "secret"
        value["tables"][0]["rows"] = [{"secret": 1}]
        self.assertNotIn("secret", str(MetadataSnapshot.model_validate(value).model_dump()))

    def test_inventory_rejects_partial_duplicate_or_missing(self):
        for value in [dict(snapshot(), complete=False), dict(snapshot(), tableIds=[]), dict(snapshot(), tableIds=[1,1])]:
            with self.assertRaises(ValidationError): MetadataSnapshot.model_validate(value)

    def test_hash_is_order_independent_but_model_sensitive(self):
        s = snapshot()
        a = canonical_table(s, s["tables"][0])
        s["tables"][0]["fields"].reverse()
        b = canonical_table(s, s["tables"][0])
        self.assertEqual(metadata_hash(a, "v1"), metadata_hash(b, "v1"))
        self.assertNotEqual(metadata_hash(a, "v1"), metadata_hash(a, "v2"))

    def test_vector_validation(self):
        for vector in ([1], [0]*768, [float("nan")]*768):
            with self.assertRaises(ValueError): normalize_vector(vector)

    def test_tokenizer_split_preserves_tail(self):
        encoder = MetadataEncoder.__new__(MetadataEncoder)
        encoder.inference_lock = threading.RLock()
        encoder.max_length = 128
        encoder.tokenizer = SimpleNamespace(encode=lambda value, add_special_tokens: list(value) + [0, 0])
        value = "结构字段"*200 + "尾部指标"
        parts = encoder.split(value, "sales.orders ")
        self.assertTrue(all(len(encoder.tokenizer.encode(p, True)) <= 128 for p in parts))
        self.assertEqual("".join(p.removeprefix("sales.orders ") for p in parts), value)

    def test_rrf_exact_priority_dedup_and_cap(self):
        entries = [dict(datasourceId=3, tableId=i, name=f"t_{i}") for i in range(80)]
        result = fuse("查询 t_79", entries, list(reversed(entries)))
        self.assertEqual(len(result), 50)
        self.assertEqual(result[0]["tableId"], 79)
        self.assertEqual(len({r["tableId"] for r in result}), 50)
        self.assertEqual(fuse("未知", [], []), [])

    def test_tail_field_is_in_routing_card(self):
        table = snapshot()["tables"][0]
        table["datasourceId"] = 3
        card = routing_card(table, {"尾部", "指标"})
        self.assertEqual(card["fields"][0]["name"], "field_25")
        self.assertLessEqual(len(card["fields"]), 8)
        table["_indexed_metadata"] = {"relations": [{"comment": "说明" * 1000}]}
        self.assertEqual(len(routing_card(table, set())["relations"][0]["comment"]), 160)

    def test_online_table_embedding_removed(self):
        source = Path("apps/chat/task/llm.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        methods = {n.name: n for n in ast.walk(module) if isinstance(n, ast.FunctionDef)}
        recall = ast.unparse(methods["_embedding_recall_logical_table_entries"])
        self.assertNotIn("embed_documents", recall)
        self.assertIn(".recall(", recall)
        self.assertNotIn("return entries", ast.unparse(methods["_recall_logical_table_entries"]))


@unittest.skipUnless(os.environ.get("SQLBOT_INDEX_TEST_DB"), "Set dedicated SQLBOT_INDEX_TEST_DB for PostgreSQL tests")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["SQLBOT_INDEX_TEST_DB"])
        if cls.engine.url.database != "sqlbot_index_test" or cls.engine.url.host not in ("127.0.0.1", "localhost"):
            raise RuntimeError("Tests require the dedicated local sqlbot_index_test database")
        with cls.engine.begin() as conn:
            Base.metadata.drop_all(conn)
            spec = importlib.util.spec_from_file_location("migration", "alembic/versions/066_platform_embeddings.py")
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()

    def setUp(self):
        with self.engine.begin() as conn:
            conn.execute(text("TRUNCATE platform_embedding_sync_job, platform_table_embedding_chunk, platform_table_embedding RESTART IDENTITY CASCADE"))
        self.encoder = FakeEncoder()
        self.index = PlatformIndex(self.engine, lambda: self.encoder)
        self.index.enqueue = lambda job_id: None
    def tearDown(self):
        self.index.close()
    def sync(self, value, force=False):
        job = self.index.submit(value["tenantId"], 7, value, force)
        self.index.run(job["jobId"])
        return self.index.get(value["tenantId"], job["jobId"])

    def test_enrichment_cannot_restore_revoked_relations_or_removed_fields(self):
        value = snapshot(2)
        value['tables'][0]['relations'] = [dict(sourceTableId=1, targetTableId=2,
            sourceTable='orders_1', targetTable='orders_2')]
        self.sync(value)
        entry = dict(datasourceId=3, tableId=1, schemaName='sales', name='orders_1',
                     fields=[dict(name='field_0')])
        result = self.index.enrich(1, [entry])[0]['_indexed_metadata']
        self.assertEqual(result['relations'], [])
        self.assertEqual([f['name'] for f in result['fields']], ['field_0'])
        renamed = self.index.enrich(1, [dict(entry, name='renamed')])[0]['_indexed_metadata']
        self.assertNotIn('comment', renamed)

    def test_100_and_300_incremental_tables(self):
        for count in (100, 300):
            s = snapshot(count, ds=count)
            self.assertEqual(self.sync(s)["added"], count)
            calls = self.encoder.calls
            self.assertEqual(self.sync(s)["skipped"], count)
            self.assertEqual(self.encoder.calls, calls)
            s["tables"][-1]["comment"] = "新说明"
            result = self.sync(s)
            self.assertEqual(result["updated"], 1)
            self.assertEqual(self.encoder.calls, calls+1)

    def test_failure_retains_old_chunks_and_sanitizes_errors(self):
        s = snapshot(2)
        self.sync(s)
        s["tables"][0]["comment"] = "changed"
        self.encoder.fail = True
        result = self.sync(s)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["failed"], 1)
        self.assertNotIn("password", str(result))
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Chunk)), 2)
            self.assertEqual(session.scalar(select(Table).where(Table.table_id==1)).metadata_json["comment"], "订单")

    def test_metadata_failure_retained_and_missing_table_deactivated(self):
        self.sync(snapshot(3))
        s = snapshot(1)
        s["tableIds"] = [1, 2]
        s["failures"] = [{"tableId":2}]
        result = self.sync(s)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["invalidated"], 1)
        with Session(self.engine) as session:
            self.assertTrue(session.scalar(select(Table).where(Table.table_id==2)).active)
            self.assertFalse(session.scalar(select(Table).where(Table.table_id==3)).active)

    def test_empty_inventory_deactivates_only_own_scope(self):
        self.sync(snapshot(2))
        self.sync(snapshot(2, tenant=2))
        result = self.sync(snapshot(0))
        self.assertEqual(result["invalidated"], 2)
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Table).where(Table.active.is_(True))), 2)

    def test_concurrent_duplicate_submission(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _: self.index.submit(1, 7, snapshot()), range(4)))
        self.assertEqual(len({j["jobId"] for j in jobs}), 1)
        self.assertIsNone(self.index.get(2, jobs[0]["jobId"]))

    def test_restart_marks_running_failed_and_requeues_pending(self):
        a = self.index.submit(1, 7, snapshot())
        b = self.index.submit(2, 8, snapshot(tenant=2))
        with Session(self.engine) as session, session.begin():
            session.get(Job, a["jobId"]).status = "running"
        queued = []
        self.index.enqueue = queued.append
        self.index.recover()
        self.assertEqual(self.index.get(1, a["jobId"])["status"], "failed")
        self.assertEqual(queued, [b["jobId"]])
        self.index.run(b["jobId"])
        self.assertEqual(self.index.get(2, b["jobId"])["status"], "succeeded")

    def test_recall_scope_model_and_query_only(self):
        self.sync(snapshot(60))
        self.sync(snapshot(tenant=2))
        entries = [dict(datasourceId=3, tableId=i) for i in range(1,61)]
        before = self.encoder.calls
        found, available = self.index.recall(1, "订单", entries)
        self.assertTrue(available)
        self.assertEqual(len(found), 50)
        self.assertEqual(self.encoder.calls, before)
        self.assertEqual(self.encoder.query_calls, 1)
        self.assertEqual(self.index.recall(3, "订单", entries), ([], False))
        self.encoder.version = "model-v2"
        self.assertEqual(self.index.recall(1, "订单", entries), ([], False))

    def test_force_rename_and_model_change(self):
        s = snapshot()
        self.sync(s)
        self.assertEqual(self.sync(s, True)["updated"], 1)
        s["tables"][0]["name"] = "renamed"
        self.assertEqual(self.sync(s)["updated"], 1)
        self.encoder.version = "model-v2"
        self.assertEqual(self.sync(s)["updated"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
