"""Read-only deployment regression for registered table identity checks."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('api', Path(__file__).with_name('verify-api.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
token = api.login('sqlbot1')
cases = [
    (1, 11, 'customers', 'public', True),
    (1, 11, 'customers', 'wrong_schema', False),
    (1, 11, 'other_table', 'public', False),
    (2, 11, 'customers', 'public', False),
    (1, None, 'customers', 'wrong_schema', False),
    (1, 11, 'wrong_schema.customers', 'public', False),
]
for datasource, table_id, name, schema, expected in cases:
    status, response = api.request('http://127.0.0.1:20184', '/openapi/sqlbot/table-permissions/check', token,
        {'datasourceId': datasource, 'tables': [{'tableId': table_id, 'name': name, 'schemaName': schema}]})
    assert response.get('code') == 0 and response['data']['allowed'] == expected, (datasource, name, schema, status)
api.A.joinpath('table-identity-regression.json').write_text(json.dumps({'passed': len(cases)}))
print('TABLE_IDENTITY_REGRESSION_OK', len(cases))
