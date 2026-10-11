"""Credential-free snapshot contract and tokenizer-aware metadata encoding."""
import hashlib
import json
import math
import threading
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

ENCODING_VERSION = "platform-chunks-v1"


class FieldMetadata(BaseModel):
    fieldId: int
    name: str
    alias: str = ""
    type: str = ""
    comment: str = ""


class RelationMetadata(BaseModel):
    sourceTableId: int
    targetTableId: int
    sourceTable: str
    targetTable: str
    sourceFieldId: int | None = None
    targetFieldId: int | None = None
    type: str = ""
    name: str = ""
    comment: str = ""


class TableMetadata(BaseModel):
    tableId: int
    categoryId: int | None = None
    schemaName: str = ""
    name: str
    categoryName: str = ""
    categoryComment: str = ""
    comment: str = ""
    fields: list[FieldMetadata]
    relations: list[RelationMetadata] = Field(default_factory=list)


class MetadataFailure(BaseModel):
    tableId: int
    # Upstream free-form exception messages are intentionally not persisted.


class MetadataSnapshot(BaseModel):
    tenantId: int
    datasourceId: int
    datasourceName: str
    datasourceType: str
    complete: bool
    tableIds: list[int]
    tables: list[TableMetadata]
    failures: list[MetadataFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_inventory(self):
        ids = [t.tableId for t in self.tables] + [f.tableId for f in self.failures]
        if not self.complete or len(ids) != len(set(ids)) or len(self.tableIds) != len(set(self.tableIds)) or set(ids) != set(self.tableIds):
            raise ValueError("Incomplete or inconsistent metadata inventory")
        return self


def canonical_table(snapshot: dict, table: dict) -> dict:
    result = TableMetadata.model_validate(table).model_dump()
    result["fields"] = sorted(result["fields"], key=lambda f: (f["name"], f["fieldId"]))
    result["relations"] = sorted(result["relations"], key=lambda r: json.dumps(r, sort_keys=True))
    result.update({k: snapshot[k] for k in ("datasourceId", "datasourceName", "datasourceType")})
    return result


def metadata_hash(metadata: dict, model_version: str) -> str:
    data = json.dumps([ENCODING_VERSION, model_version, metadata], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def normalize_vector(vector):
    values = [float(v) for v in vector]
    if len(values) != 768 or not all(math.isfinite(v) for v in values):
        raise ValueError("Embedding must contain 768 finite values")
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise ValueError("Zero embedding")
    return [v / norm for v in values]


class MetadataEncoder:
    def __init__(self, model, model_path: str):
        self.model = model
        self.inference_lock = getattr(model, "inference_lock", threading.RLock())
        client = getattr(model, "_client", None) or getattr(model, "client", None)
        self.tokenizer = client.tokenizer
        self.max_length = int(client.max_seq_length)
        if client.get_sentence_embedding_dimension() != 768 or self.max_length <= 8:
            raise ValueError("Incompatible local embedding model")
        root = Path(model_path)
        digest = hashlib.sha256(ENCODING_VERSION.encode())
        # Hash local weights and tokenizer/config, not timestamps or absolute paths.
        files = sorted(p for p in root.rglob("*") if p.is_file() and
                       (p.suffix in (".json", ".txt", ".bin", ".safetensors")) and
                       "onnx" not in p.parts and p.name != "logs.txt")
        if not any(p.suffix in (".bin", ".safetensors") for p in files):
            raise ValueError("Local model weights are missing")
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode())
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        self.version = digest.hexdigest()

    def _token_length(self, text):
        with self.inference_lock:
            return len(self.tokenizer.encode(text, add_special_tokens=True))

    def split(self, text: str, prefix: str = "") -> list[str]:
        """Split by characters with tokenizer checks; never lose text by decode/encode."""
        if not text.strip():
            return []
        # A long identity remains in the summary; repeated context gets a bounded prefix.
        while prefix and self._token_length(prefix) > self.max_length // 3:
            prefix = prefix[:len(prefix) // 2]
        result = []
        while text:
            lo, hi = 1, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._token_length(prefix + text[:mid]) <= self.max_length:
                    lo = mid
                else:
                    hi = mid - 1
            fragment = prefix + text[:lo]
            if self._token_length(fragment) > self.max_length:
                raise ValueError("Cannot encode metadata within model length")
            result.append(fragment)
            text = text[lo:]
        return result

    def chunks(self, metadata: dict) -> list[str]:
        identity = f"{metadata['schemaName']}.{metadata['name']} "
        summary = "\n".join(str(metadata.get(k) or "") for k in
                            ("datasourceName", "datasourceType", "schemaName", "name", "categoryName", "categoryComment", "comment"))
        result = self.split(summary)
        pending = ""
        for field in metadata["fields"]:
            line = " | ".join(str(field.get(k) or "") for k in ("name", "alias", "type", "comment"))
            combined = (pending + "\n" + line).strip()
            if pending and self._token_length(identity + combined) > self.max_length:
                result.extend(self.split(pending, identity))
                pending = line
            else:
                pending = combined
        result.extend(self.split(pending, identity))
        for relation in metadata.get("relations", []):
            result.extend(self.split(json.dumps(relation, ensure_ascii=False, sort_keys=True), identity))
        return result

    def documents(self, chunks: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(chunks), 32):
            with self.inference_lock:
                batch = self.model.embed_documents(chunks[start:start + 32])
            if len(batch) != len(chunks[start:start + 32]):
                raise ValueError("Embedding batch size mismatch")
            vectors.extend(normalize_vector(v) for v in batch)
        return vectors

    def query(self, question: str) -> list[float]:
        chunks = self.split(question)
        if not chunks:
            raise ValueError("Empty question")
        vectors = []
        for chunk in chunks:
            with self.inference_lock:
                vectors.append(normalize_vector(self.model.embed_query(chunk)))
        return normalize_vector([sum(v[i] for v in vectors) / len(vectors) for i in range(768)])
