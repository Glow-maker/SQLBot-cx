"""Bounded candidate fusion and compact LLM routing payloads."""
import re


def table_key(entry):
    return str(entry.get("datasourceId")), str(entry.get("tableId"))


def exact_name(question, entry):
    return any(name and re.search(r"(?<![\w])" + re.escape(str(name).lower()) + r"(?![\w])", question.lower())
               for name in (entry.get("name"), entry.get("qualifiedName")))


def fuse(question, keyword, vector, limit=50):
    scores, entries = {}, {}
    for ranked in (keyword, vector):
        seen = set()
        for rank, entry in enumerate(ranked, 1):
            key = table_key(entry)
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            entries[key] = entry
    keys = sorted(entries, key=lambda k: (not exact_name(question, entries[k]), -scores[k], k))
    return [entries[k] for k in keys[:max(1, min(limit, 50))]]


def routing_card(entry, terms):
    card = {k: entry.get(k) for k in ("datasourceId", "datasourceName", "tableId", "schemaName", "name", "qualifiedName")}
    card["comment"] = str(entry.get("comment") or "")[:400]
    metadata = entry.get("_indexed_metadata") or {}
    fields = entry.get("fields") or []
    field_by_name = {f["name"]: f for f in metadata.get("fields", [])}
    field_by_name.update({f["name"]: f for f in fields})
    fields = list(field_by_name.values())
    def score(field):
        value = " ".join(str(field.get(k) or "") for k in ("name", "alias", "comment")).lower()
        return sum(len(t) for t in terms if t in value)
    fields.sort(key=lambda f: (-score(f), str(f.get("name"))))
    card["fields"] = [{k: str(f.get(k) or "")[:160] for k in ("name", "alias", "type", "comment")} for f in fields[:8]]
    card["relations"] = [{k: v[:160] if isinstance(v, str) else v for k, v in relation.items()}
                         for relation in metadata.get("relations", [])[:8]]
    return card
