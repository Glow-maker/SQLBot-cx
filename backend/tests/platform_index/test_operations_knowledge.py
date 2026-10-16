import unittest
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import sqlbot_xpack  # Match main.py initialization order for the bundled extension.
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Integer, JSON, MetaData, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session

from apps.operations import knowledge as api
from apps.operations.knowledge_models import Base, KnowledgeCatalog, KnowledgeEntry
from apps.operations.models import OperationsGrant, ReportTask


SOURCE = {'id': 24, 'name': '业务库', 'type': 'pg', 'tables': [
    {'tableId': 7, 'schemaName': 'sales', 'name': 'orders', 'categoryId': 70,
     'fields': [{'name': 'amount', 'type': 'numeric'}, {'name': 'id', 'type': 'int'}]},
    {'tableId': 8, 'schemaName': 'sales', 'name': 'secrets', 'fields': [{'name': 'salary'}]},
]}


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        OperationsGrant.__table__.create(self.engine)
        ReportTask.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.admin = SimpleNamespace(id=1, oid=1, auth_source='local', isAdmin=True)
        self.operator = SimpleNamespace(id=31, oid=124, auth_source='platform', isAdmin=False)
        self.session.add(OperationsGrant(tenant_id=124, user_id=31, enabled=True, updated_by=1))
        self.session.commit()
        api.merge_catalog(self.session, 124, [SOURCE])
        self.embedding = patch.object(api.settings, 'EMBEDDING_ENABLED', False)
        self.embedding.start()
        self.addCleanup(self.embedding.stop)

    def add(self, kind='data-training', tenant=124, enabled=True, **values):
        payload = ({'question': '订单金额', 'description': 'SELECT amount FROM sales.orders', 'datasource': 24}
                   if kind == 'data-training' else {'word': '订单', 'description': '订单是销售订单',
                       'other_words': [], 'specific_ds': True, 'datasource_ids': [24]})
        payload.update(values)
        row = KnowledgeEntry(source_kind='platform', tenant_id=tenant, kind=kind,
            label=payload.get('question') or payload['word'], payload=payload, enabled=enabled)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def service(self, question='订单金额'):
        selected = dict(SOURCE['tables'][0], datasourceId=24, datasourceType='pg')
        return SimpleNamespace(current_user=self.operator, logical_route_tables=[selected],
            chat_question=SimpleNamespace(question=question), record=SimpleNamespace(id=10, chat_id=2),
            _extract_authorization_header=lambda: 'Bearer current-user', _check_deadline=lambda: None)

    def test_catalog_whitelist_has_no_credentials_or_samples(self):
        value = dict(SOURCE, host='secret-host', password='secret-password', token='secret-token',
                     physicalDatasourceKey='secret-key', configuration='secret-config')
        value['tables'] = [dict(SOURCE['tables'][0], rule='secret-rule', sql='secret-sql',
            fields=[dict(SOURCE['tables'][0]['fields'][0], sample='secret-sample')])]
        sanitized = api.clean_catalog([value])
        self.assertNotIn('secret-', str(sanitized))
        self.assertEqual(sanitized[0]['tables'][0]['fields'], [{'name': 'amount', 'type': 'numeric'}])

    def test_partial_catalog_never_deletes_other_observed_tables_or_tenants(self):
        api.merge_catalog(self.session, 125, [dict(SOURCE, name='另一租户')])
        api.merge_catalog(self.session, 124, [dict(SOURCE, tables=[SOURCE['tables'][0]])])
        self.assertEqual(len(api.catalog_rows(self.session, 124)[0].metadata_json['tables']), 2)
        self.assertEqual(api.catalog_rows(self.session, 125)[0].metadata_json['name'], '另一租户')

    def test_local_admin_reads_platform_snapshot_but_cannot_refresh(self):
        result = api.catalog(self.session, self.admin, scope='platform', tenant_id=124)
        self.assertFalse(result['can_refresh'])
        self.assertEqual(result['sources'][0]['id'], 24)

    def test_granted_platform_operator_cannot_cross_tenant(self):
        with self.assertRaises(HTTPException) as caught:
            api.catalog(self.session, self.operator, scope='platform', tenant_id=125)
        self.assertEqual(caught.exception.status_code, 403)

    def test_ungranted_platform_admin_flag_is_not_operations_authority(self):
        user = SimpleNamespace(id=99, oid=124, auth_source='platform', isAdmin=True)
        with self.assertRaises(HTTPException):
            api.catalog(self.session, user, scope='platform', tenant_id=124)

    def test_new_entry_is_draft_even_when_client_requests_enabled(self):
        entry_id = api.save('terminology', {'word': '订单', 'description': '销售订单', 'enabled': True},
            BackgroundTasks(), self.session, self.operator, lambda x: x, scope='platform')
        row = self.session.get(KnowledgeEntry, entry_id)
        self.assertFalse(row.enabled)
        self.assertEqual((row.tenant_id, row.source_kind), (124, 'platform'))

    def test_catalog_filter_includes_global_and_matching_sources_only(self):
        expected = self.add(kind='terminology', datasource_ids=[24, 25])
        global_term = self.add(kind='terminology', specific_ds=False, datasource_ids=[])
        self.add(kind='terminology', datasource_ids=[240])
        response = api.pager('terminology', 1, 10, self.session, self.operator,
                             scope='platform', dslist=[24])
        self.assertEqual(response['total_count'], 2)
        self.assertEqual({int(row['id']) for row in response['data']}, {expected.id, global_term.id})

    def test_recall_applies_datasource_scope_before_candidate_limit(self):
        expected = self.add()
        for index in range(201):
            self.session.add(KnowledgeEntry(source_kind='platform', tenant_id=124,
                kind='data-training', label='订单金额', enabled=True,
                payload={'question': '订单金额', 'description': 'SELECT amount FROM sales.orders',
                         'datasource': 240 + index}))
        self.session.commit()
        result = api.recall_entries(self.session, 124, 'data-training', '订单金额', {24})
        self.assertEqual([row.id for row in result], [expected.id])

    def test_wrong_tenant_cannot_edit_delete_or_enable_by_id(self):
        row = self.add(tenant=125)
        for operation in (
            lambda: api.save('data-training', dict(row.payload, id=row.id), BackgroundTasks(),
                             self.session, self.operator, lambda x: x, scope='platform'),
            lambda: api.remove('data-training', self.session, self.operator, [row.id], scope='platform'),
            lambda: api.enable('data-training', row.id, api.EnableBody(enabled=False), self.session,
                               self.operator, lambda x: x, scope='platform'),
        ):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 404)
        self.assertIsNotNone(self.session.get(KnowledgeEntry, row.id))

    def test_save_rejects_writes_unknown_tables_and_columns(self):
        for sql in ('DELETE FROM sales.orders', 'SELECT amount FROM sales.missing',
                    'SELECT salary FROM sales.orders', 'SELECT 1; SELECT amount FROM sales.orders',
                    "SELECT pg_read_file('/etc/passwd') FROM sales.orders",
                    "SELECT set_config('search_path', 'evil', false) FROM sales.orders",
                    'SELECT pg_terminate_backend(id) FROM sales.orders'):
            with self.subTest(sql=sql), self.assertRaises(HTTPException):
                api.save('data-training', {'question': '订单', 'description': sql, 'datasource': 24},
                         BackgroundTasks(), self.session, self.operator, lambda x: x, scope='platform')

    def test_cte_readonly_sample_resolves_only_physical_tables(self):
        refs = api.validate_sql('WITH x AS (SELECT amount FROM sales.orders) SELECT sum(amount) FROM x',
                                SOURCE['tables'], 'pg')
        self.assertEqual([t['name'] for t in refs], ['orders'])

    def test_dm_example_can_be_saved_as_draft(self):
        api.merge_catalog(self.session, 124, [dict(SOURCE, type='dm')])
        item_id = api.save('data-training', {'question': '订单', 'description':
            'SELECT "amount" FROM "sales"."orders" WHERE 1 = 0', 'datasource': 24},
            BackgroundTasks(), self.session, self.operator, lambda x: x, scope='platform')
        self.assertFalse(self.session.get(KnowledgeEntry, item_id).enabled)
        self.assertEqual(api.catalog_rows(self.session, 124)[0].metadata_json['type'], 'dm')

    def test_dm_examples_still_reject_writes_and_out_of_scope_columns(self):
        api.merge_catalog(self.session, 124, [dict(SOURCE, type='dm')])
        for sql, message in (
            ('DELETE FROM "sales"."orders"', '只允许单条只读查询'),
            ('SELECT "salary" FROM "sales"."orders"', '不存在的字段'),
            ('SELECT "amount" FROM "sales"."missing"', '范围外'),
        ):
            with self.subTest(sql=sql), self.assertRaises(HTTPException) as caught:
                api.save('data-training', {'question': '订单', 'description': sql, 'datasource': 24},
                    BackgroundTasks(), self.session, self.operator, lambda x: x, scope='platform')
            self.assertEqual(caught.exception.status_code, 422)
            self.assertIn(message, caught.exception.detail)

    def test_platform_dialect_aliases_parse_real_identifier_syntax(self):
        for kind in ('pg', 'postgresql', 'opengauss', 'kingbase', 'excel', 'dm', 'ck', 'mariadb', 'sqlServer'):
            quote = '`' if kind in ('ck', 'mariadb') else '"'
            sql = f'SELECT {quote}amount{quote} FROM {quote}sales{quote}.{quote}orders{quote}'
            if kind == 'sqlServer':
                sql = 'SELECT TOP 10 [amount] FROM [sales].[orders]'
            with self.subTest(kind=kind):
                refs = api.validate_sql(sql, SOURCE['tables'], kind)
                self.assertEqual([t['name'] for t in refs], ['orders'])

    def test_unknown_platform_dialect_is_rejected_without_generic_fallback(self):
        with self.assertRaisesRegex(ValueError, 'Unknown dialect'):
            api.validate_sql('SELECT amount FROM sales.orders', SOURCE['tables'], 'unknown_database')

    def test_draft_and_other_tenant_entries_do_not_reach_prompt(self):
        self.add(enabled=False)
        self.add(tenant=125)
        with patch.object(api, 'check_table_permissions') as remote:
            self.assertEqual(api.platform_templates(self.service(), self.session), ('', [], '', []))
        remote.assert_not_called()

    def test_permission_denied_or_error_never_injects_example(self):
        self.add()
        self.add(kind='terminology')
        with patch.object(api, 'check_table_permissions', return_value=SimpleNamespace(allowed=False)):
            self.assertEqual(api.platform_templates(self.service(), self.session), ('', [], '', []))
        with patch.object(api, 'check_table_permissions', side_effect=RuntimeError('unavailable')):
            self.assertEqual(api.platform_templates(self.service(), self.session), ('', [], '', []))

    def test_no_authorized_source_does_not_inject_global_terms(self):
        self.add(kind='terminology', specific_ds=False, datasource_ids=[])
        service = self.service()
        with patch.object(api, 'check_table_permissions', return_value=SimpleNamespace(allowed=False)):
            self.assertEqual(api.platform_templates(service, self.session), ('', [], '', []))
        self.assertEqual(service.operations_knowledge, [])

    def test_optional_query_embedding_is_bounded_and_never_queues_stalled_jobs(self):
        from apps.ai_model.embedding import EmbeddingModelCache
        release, finished = threading.Event(), threading.Event()
        def stalled(_):
            release.wait(2)
            finished.set()
            return [1.0]
        service = self.service()
        service.query_deadline = time.monotonic() + .03
        service._check_deadline = MagicMock()
        try:
            with patch.object(EmbeddingModelCache, 'get_model', return_value=SimpleNamespace(embed_query=stalled)) as model:
                started = time.monotonic()
                self.assertIsNone(api.query_vector(service))
                self.assertLess(time.monotonic() - started, .5)
                self.assertIsNone(api.query_vector(service))
                model.assert_called_once()
                self.assertGreaterEqual(service._check_deadline.call_count, 3)
        finally:
            release.set()
            self.assertTrue(finished.wait(2))

    def test_outside_selected_table_example_is_never_injected(self):
        self.add(description='SELECT salary FROM sales.secrets')
        with patch.object(api, 'check_table_permissions', return_value=SimpleNamespace(allowed=True)):
            result = api.platform_templates(self.service(), self.session)
        self.assertEqual(result[3], [])
        self.assertNotIn('salary', result[2])

    def test_valid_example_uses_current_token_and_one_batch_check_then_is_cached(self):
        self.add()
        service = self.service()
        with patch.object(api, 'check_table_permissions', return_value=SimpleNamespace(allowed=True)) as remote:
            result = api.platform_templates(service, self.session)
            self.assertEqual(result, api.platform_templates(service, self.session))
        remote.assert_called_once()
        self.assertEqual(remote.call_args.kwargs['authorization_header'], 'Bearer current-user')
        self.assertEqual(remote.call_args.kwargs['tenant_id'], 124)
        self.assertEqual(len(remote.call_args.kwargs['tables']), 1)
        self.assertIn('SELECT amount FROM sales.orders', result[2])
        self.assertEqual(service.operations_knowledge[0]['source_kind'], 'platform')

    def test_early_call_without_selection_does_not_cache_empty_result(self):
        self.add()
        service = self.service()
        selected = service.logical_route_tables
        service.logical_route_tables = []
        self.assertEqual(api.platform_templates(service, self.session), ('', [], '', []))
        service.logical_route_tables = selected
        with patch.object(api, 'check_table_permissions', return_value=SimpleNamespace(allowed=True)):
            self.assertEqual(len(api.platform_templates(service, self.session)[3]), 1)

    def test_prompt_counts_are_bounded(self):
        for i in range(8):
            self.add(question='订单金额', description=f'SELECT amount FROM sales.orders LIMIT {i + 1}')
            self.add(kind='terminology', word='订单')
        with patch.object(api, 'check_table_permissions', return_value=SimpleNamespace(allowed=True)):
            result = api.platform_templates(self.service(), self.session)
        self.assertEqual((len(result[1]), len(result[3])), (5, 3))

    def test_report_draft_confirmation_is_same_transaction_and_cannot_repeat(self):
        report = ReportTask(scope='platform', tenant_id=124, auth_source='platform', created_by=31,
            status='completed', range_start=datetime.utcnow() - timedelta(days=1), range_end=datetime.utcnow(),
            result_json={'drafts': [{'draft_id': 'd1', 'status': 'pending', 'kind': 'terminology'}]})
        self.session.add(report)
        self.session.commit()
        self.session.refresh(report)
        body = {'word': '订单', 'description': '销售订单', 'report_id': report.id, 'draft_id': 'd1'}
        item_id = api.save('terminology', body, BackgroundTasks(), self.session, self.operator,
                           lambda x: x, scope='platform')
        self.session.refresh(report)
        draft = report.result_json['drafts'][0]
        self.assertEqual((draft['status'], draft['knowledge_id']), ('accepted', item_id))
        self.assertFalse(self.session.get(KnowledgeEntry, item_id).enabled)
        with self.assertRaises(HTTPException) as caught:
            api.save('terminology', body, BackgroundTasks(), self.session, self.operator,
                     lambda x: x, scope='platform')
        self.assertEqual(caught.exception.status_code, 409)

    def test_local_source_keeps_old_storage_and_scope_checks(self):
        from apps.datasource.models.datasource import CoreDatasource
        from apps.data_training.models.data_training_model import DataTraining
        from apps.terminology.models.terminology_model import Terminology
        # Clone DDL for an in-memory SQLite fixture; production ORM definitions stay unchanged.
        metadata = MetaData()
        for model in (CoreDatasource, DataTraining, Terminology):
            table = model.__table__.to_metadata(metadata)
            table.c.id.type = Integer()
            for column in table.columns:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
        metadata.create_all(self.engine)
        self.session.add(CoreDatasource(id=1, name='本地源', type='pg', configuration='{}', oid=1))
        self.session.commit()
        item_id = api.save('data-training', {'question': '销售额', 'description': 'SELECT amount FROM orders',
            'datasource': 1, 'enabled': True}, BackgroundTasks(), self.session, self.admin,
            lambda x: x, scope='local', tenant_id=1)
        item = self.session.get(DataTraining, item_id)
        self.assertEqual(item.oid, 1)
        self.assertFalse(item.enabled)
        api.enable('data-training', item_id, api.EnableBody(enabled=True), self.session,
                   self.admin, lambda x: x, scope='local', tenant_id=1)
        self.session.refresh(item)
        self.assertTrue(item.enabled)
        with self.assertRaises(HTTPException):
            api.remove('data-training', self.session, self.admin, [item_id], scope='local', tenant_id=2)
        api.remove('data-training', self.session, self.admin, [item_id], scope='local', tenant_id=1)
        self.assertIsNone(self.session.get(DataTraining, item_id))


class CatalogRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_admin_never_forwards_local_jwt(self):
        user = SimpleNamespace(id=1, oid=1, auth_source='local', isAdmin=True)
        with patch.object(api.httpx, 'AsyncClient') as client:
            with self.assertRaises(HTTPException) as caught:
                await api.refresh_catalog(SimpleNamespace(headers={'Authorization': 'Bearer local'}),
                                          MagicMock(), user, scope='platform', tenant_id=124)
        self.assertEqual(caught.exception.status_code, 403)
        client.assert_not_called()

    async def test_platform_refresh_uses_old_endpoint_and_current_identity(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'code': 0, 'data': [dict(SOURCE, password='must-not-persist-password')]})
        remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        user = SimpleNamespace(id=31, oid=124, auth_source='platform', isAdmin=False)
        with patch.object(api, 'require_operator', return_value=('platform', 124)), \
             patch.object(api.settings, 'PLATFORM_DATASOURCE_ENABLED', True), \
             patch.object(api.settings, 'PLATFORM_DATASOURCE_BASE_URL', 'http://data.test'), \
             patch.object(api.httpx, 'AsyncClient', return_value=remote), \
             patch.object(api, 'merge_catalog') as merge:
            await api.refresh_catalog(SimpleNamespace(headers={'Authorization': 'Bearer platform'}),
                                      MagicMock(), user, scope='platform', tenant_id=124)
        self.assertTrue(str(calls[0].url).endswith('/openapi/sqlbot/datasources/query'))
        self.assertEqual(calls[0].headers['tenant-id'], '124')
        self.assertEqual(calls[0].headers['authorization'], 'Bearer platform')
        self.assertNotIn('must-not-persist-password', str(merge.call_args))


class LegacyKnowledgeAccessTests(unittest.TestCase):
    def test_actual_legacy_routers_reject_nonlocal_identity_before_endpoint_dependencies(self):
        from apps.data_training.api.data_training import router as training_router
        from apps.terminology.api.terminology import router as terminology_router
        from common.core.deps import get_current_user, get_session
        app = FastAPI()
        app.include_router(training_router)
        app.include_router(terminology_router)
        def no_database():
            raise AssertionError('Nonlocal identity must be rejected before reading local storage')
        app.dependency_overrides[get_session] = no_database
        with TestClient(app) as client:
            for source in ('platform', 'ask', 'assistant', 'unknown'):
                app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
                    id=1, oid=1, isAdmin=True, auth_source=source)
                for prefix in ('/system/terminology', '/system/data-training'):
                    for method, path in (('GET', '/page/1/10'), ('PUT', ''), ('DELETE', ''),
                                         ('GET', '/1/enable/true'), ('GET', '/export'),
                                         ('GET', '/template'), ('POST', '/uploadExcel')):
                        with self.subTest(source=source, path=prefix + path, method=method):
                            response = client.request(method, prefix + path)
                            self.assertEqual(response.status_code, 403)

    def test_trusted_local_identity_keeps_legacy_route_access(self):
        from apps.terminology.api.terminology import router
        from common.core.deps import get_current_user, get_i18n
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(auth_source='local')
        app.dependency_overrides[get_i18n] = lambda: (lambda key: key)
        with TestClient(app) as client:
            self.assertEqual(client.get('/system/terminology/template').status_code, 200)


if __name__ == '__main__':
    unittest.main()
