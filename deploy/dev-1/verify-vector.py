"""Run inside the SQLBot container: real local embedding and scoped vector recall."""
import json
from apps.datasource.embedding.platform_index import get_platform_index

index = get_platform_index()
entries = [{'datasourceId': 1, 'tableId': 11}, {'datasourceId': 1, 'tableId': 12}]
try:
    result, indexed = index.recall(124, '客户订单金额', entries)
    assert indexed and result and all(row in entries for row in result)
    other, indexed_other = index.recall(999, '客户订单金额', entries)
    assert other == [] and not indexed_other
    none, indexed_none = index.recall(124, '客户订单金额', [])
    assert none == [] and not indexed_none
    print(json.dumps({'vectorRecall': 'passed', 'tenantIsolation': 'passed', 'permissionScope': 'passed',
                      'modelVersion': index.encoder().version, 'candidates': result}))
finally:
    index.close()
