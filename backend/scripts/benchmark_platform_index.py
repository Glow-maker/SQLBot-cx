"""Synthetic local-model benchmark. Never interpret this as business recall acceptance.

Uses only the dedicated local sqlbot_index_test DB and synthetic tenant 991.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from sqlalchemy import create_engine
from langchain_huggingface import HuggingFaceEmbeddings
from apps.datasource.embedding.platform_index import PlatformIndex
from apps.datasource.embedding.platform_text import MetadataEncoder, canonical_table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--tables", type=int, default=100)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    engine = create_engine(os.environ["SQLBOT_INDEX_TEST_DB"])
    if engine.url.database != "sqlbot_index_test" or engine.url.host not in ("127.0.0.1", "localhost"):
        raise RuntimeError("Dedicated local test database required")
    begin = time.perf_counter()
    model = HuggingFaceEmbeddings(model_name=args.model_path, model_kwargs={"device":"cpu"}, encode_kwargs={"normalize_embeddings":True})
    encoder = MetadataEncoder(model, args.model_path)
    load_ms = (time.perf_counter()-begin)*1000
    tables = [dict(tableId=i, schemaName="sales", name=f"purchase_order_{i}", comment="采购订单记录",
                   fields=[dict(fieldId=i*100+j, name=f"field_{j}", type="BIGINT", comment="采购编号" if j<25 else "供应商逾期天数")
                           for j in range(26)]) for i in range(1,args.tables+1)]
    snapshot = dict(tenantId=991, datasourceId=args.tables, datasourceName="合成采购仓库", datasourceType="pg",
                    complete=True, tableIds=[t["tableId"] for t in tables], tables=tables, failures=[])
    metadata = canonical_table(snapshot, tables[0])
    chunks = encoder.chunks(metadata)
    assert all(len(encoder.tokenizer.encode(c, add_special_tokens=True)) <= encoder.max_length for c in chunks)
    assert any("供应商逾期天数" in c for c in chunks)
    index = PlatformIndex(engine, lambda: encoder)
    index.enqueue = lambda job_id: None
    begin = time.perf_counter()
    job = index.submit(991, 1, snapshot, force=True)
    index.run(job["jobId"])
    sync_ms = (time.perf_counter()-begin)*1000
    result = index.get(991, job["jobId"])
    assert result["status"] == "succeeded", result
    question = "采购订单的供应商逾期天数是多少"
    entries = [dict(datasourceId=args.tables, tableId=t["tableId"]) for t in tables]
    # Reproduce the removed online work: embed the first 20 field preview of every table.
    old_texts = [t["name"] + " " + t["comment"] + " " + " ".join(f["name"]+f["comment"] for f in t["fields"][:20]) for t in tables]
    begin = time.perf_counter()
    model.embed_documents(old_texts)
    model.embed_query(question)
    legacy_ms = (time.perf_counter()-begin)*1000
    timings = []
    for _ in range(10):
        begin = time.perf_counter()
        found, available = index.recall(991, question, entries)
        timings.append((time.perf_counter()-begin)*1000)
        assert available and len(found) <= 50
    index.close()
    report = dict(evidence="synthetic_local_model_postgresql_not_business_acceptance", tables=args.tables,
                  model_version=encoder.version, dimension=768, max_length=encoder.max_length,
                  model_load_ms=load_ms, sync_ms=sync_ms, chunks_per_table=len(chunks),
                  legacy_single_sample_ms=legacy_ms, recall_samples_ms=timings,
                  recall_p50_ms=sorted(timings)[4], recall_p95_ms=max(timings),
                  tail_field_in_chunks=True, task=result)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("task", "recall_samples_ms")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
