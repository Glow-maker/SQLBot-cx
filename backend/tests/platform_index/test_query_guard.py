import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from apps.chat.task.query_guard import bounded_stream, is_greeting, require_read_query
from apps.chat.task.llm import LLMService
from apps.chat.task.record_access import require_record_access, require_current_candidates
from fastapi import HTTPException


class StoredExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoked_export_never_reads_stored_data(self):
        import inspect
        from apps.chat.api.chat import export_excel
        with patch('apps.chat.task.record_access.require_record_access', side_effect=HTTPException(403, 'revoked')) as access, \
             patch('apps.chat.api.chat.get_chat_chart_data') as read:
            with self.assertRaises(HTTPException):
                await inspect.unwrap(export_excel)(MagicMock(), SimpleNamespace(id=1, oid=124), 10, 5, lambda x: x)
            read.assert_not_called()
            self.assertIs(access.call_args.kwargs.get('check_candidates'), False)


class QueryGuardTests(unittest.TestCase):
    def test_platform_missing_permission_endpoint_is_denied(self):
        service = LLMService.__new__(LLMService)
        with patch('apps.chat.task.llm.is_permission_check_enabled', return_value=False), \
             patch('apps.chat.task.llm.settings.PLATFORM_DATASOURCE_ENABLED', True):
            self.assertFalse(service._do_permission_check('SELECT * FROM orders', None, 'gate2').allowed)

    def test_request_log_and_message_state_is_not_shared(self):
        sessions = [MagicMock(), MagicMock()]
        services = [LLMService.__new__(LLMService), LLMService.__new__(LLMService)]
        for service, session in zip(services, sessions):
            session.get.return_value = None
            with self.assertRaises(Exception):
                service.__init__(session, SimpleNamespace(id=1, oid=124), SimpleNamespace(chat_id=5))
        services[0].current_logs['route'] = 'private-first-request'
        services[0].sql_message.append('private-schema')
        self.assertEqual(services[1].current_logs, {})
        self.assertEqual(services[1].sql_message, [])

    def test_stored_candidates_cannot_leak_revoked_table_or_removed_field(self):
        import json
        entry = dict(datasourceId=1, tableId=12, schemaName='public', name='orders', fields=[dict(name='amount')])
        messages = [dict(type='human', content=json.dumps(dict(tables=[entry])))]
        require_current_candidates(messages, [entry])
        for current in ([], [dict(entry, tableId=22)], [dict(entry, name='renamed')],
                        [dict(entry, fields=[dict(name='other')])]):
            with self.assertRaises(HTTPException):
                require_current_candidates(messages, current)
        # Data returns no structure when it cannot read an unregistered table; the table is still granted.
        require_current_candidates(messages, [dict(entry, fields=[])])

    def test_viewing_results_rechecks_sql_but_not_routing_candidates(self):
        record = SimpleNamespace(id=10, create_by=1, chat_id=5, datasource=2, sql='SELECT * FROM orders')
        ds = SimpleNamespace(id=2, physicalDatasourceKey='source:second')
        for check_candidates in (False, True):
            session = MagicMock()
            session.get.side_effect = [record, SimpleNamespace(create_by=1, oid=124)]
            session.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(messages=[])]
            service = MagicMock()
            service._ensure_out_ds_instance.return_value = SimpleNamespace(ds_list=[ds])
            service._do_permission_check.return_value = SimpleNamespace(allowed=True)
            with patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', True), \
                 patch('apps.chat.task.record_access.RequestContext.get_request', return_value=SimpleNamespace(state=SimpleNamespace(assistant=object()))), \
                 patch('apps.chat.task.llm.LLMService.__new__', return_value=service), \
                 patch('apps.chat.task.record_access.require_current_candidates', side_effect=HTTPException(403, 'changed')) as candidates:
                if check_candidates:
                    with self.assertRaises(HTTPException):
                        require_record_access(session, SimpleNamespace(id=1, oid=124), 10)
                else:
                    self.assertIs(require_record_access(session, SimpleNamespace(id=1, oid=124), 10, check_candidates=False), record)
                    candidates.assert_not_called()
                    service._do_permission_check.assert_called_once()

    def test_stored_chart_and_predict_data_skip_candidate_check(self):
        from apps.chat.curd.chat import get_chart_data_with_user, get_chat_predict_data_with_user
        for read in (get_chart_data_with_user, get_chat_predict_data_with_user):
            session = MagicMock()
            session.execute.return_value = []
            with patch('apps.chat.task.record_access.require_record_access') as access:
                self.assertEqual(read(session, SimpleNamespace(id=1, oid=124), 10), {})
            self.assertIs(access.call_args.kwargs.get('check_candidates'), False)

    def test_cte_alias_cannot_hide_qualified_physical_table(self):
        from common.utils.sql_parser import extract_table_refs
        refs = extract_table_refs('WITH orders AS (SELECT * FROM customers) SELECT * FROM public.orders', 'postgres')
        self.assertEqual({(r.schema, r.name) for r in refs}, {(None, 'customers'), ('public', 'orders')})

    def test_same_host_different_database_is_not_one_query_source(self):
        from apps.system.crud.assistant import AssistantOutDs
        source = dict(type='pg', host='host', port=5432, dataBase='a', user='reader')
        key = AssistantOutDs._build_physical_datasource_key(source, 1)
        self.assertNotEqual(key, AssistantOutDs._build_physical_datasource_key(dict(source, dataBase='b'), 2))
        self.assertNotEqual(key, AssistantOutDs._build_physical_datasource_key(dict(source, user='other'), 2))
        self.assertEqual(key, AssistantOutDs._build_physical_datasource_key(dict(source, db_schema='other_schema'), 2))

    def test_greetings_do_not_swallow_business_questions(self):
        for value in ('你好！', ' hello ', '谢谢'):
            self.assertTrue(is_greeting(value))
        for value in ('你好，统计订单金额', '今天金额是多少', 'hi orders'):
            self.assertFalse(is_greeting(value))

    def test_single_read_queries(self):
        for sql in ('SELECT o.amount FROM orders o', 'WITH x AS (SELECT * FROM orders) SELECT * FROM x',
                    'SELECT * FROM (SELECT * FROM orders) x', 'SELECT id FROM orders UNION SELECT id FROM customers'):
            require_read_query(sql, 'postgres')
        for sql in ('DELETE FROM orders', 'SELECT 1; SELECT 2', 'SELECT * INTO copy FROM orders',
                    'WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x', 'SELECT * FROM orders FOR UPDATE'):
            with self.assertRaises(ValueError):
                require_read_query(sql, 'postgres')

    def test_stalled_model_times_out_without_waiting_for_first_token(self):
        def stalled():
            time.sleep(.2)
            yield 'late'
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            list(bounded_stream(stalled, started+.03))
        self.assertLess(time.monotonic()-started, .15)

    def test_absolute_deadline_stops_continuous_tokens(self):
        def stream():
            while True:
                time.sleep(.005)
                yield 'token'
        with self.assertRaises(TimeoutError):
            list(bounded_stream(stream, time.monotonic()+.03))

    def test_late_request_never_reaches_database(self):
        service = LLMService.__new__(LLMService)
        service.query_deadline = time.monotonic()-1
        with patch('apps.chat.task.llm.exec_sql') as execute:
            with self.assertRaises(TimeoutError):
                service.execute_sql('SELECT * FROM orders')
            execute.assert_not_called()

    def test_foreign_record_and_tenant_are_rejected(self):
        for owner, tenant in ((2, 124), (1, 999)):
            session = MagicMock()
            session.get.side_effect = [SimpleNamespace(create_by=owner, chat_id=5),
                                       SimpleNamespace(create_by=owner, oid=tenant)]
            with self.assertRaises(HTTPException):
                require_record_access(session, SimpleNamespace(id=1, oid=124), 10)

    def test_stored_result_resolves_its_original_source_and_rechecks_grant(self):
        record = SimpleNamespace(id=10, create_by=1, chat_id=5, datasource=2, sql='SELECT * FROM orders')
        ds = SimpleNamespace(id=2, physicalDatasourceKey='source:second')
        service = MagicMock()
        service._ensure_out_ds_instance.return_value = SimpleNamespace(ds_list=[ds])
        for allowed in (True, False):
            session = MagicMock()
            session.get.side_effect = [record, SimpleNamespace(create_by=1, oid=124)]
            service._do_permission_check.return_value = SimpleNamespace(allowed=allowed)
            with patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', True), \
                 patch('apps.chat.task.record_access.RequestContext.get_request', return_value=SimpleNamespace(state=SimpleNamespace(assistant=object()))), \
                 patch('apps.chat.task.llm.LLMService.__new__', return_value=service):
                if allowed:
                    self.assertIs(require_record_access(session, SimpleNamespace(id=1, oid=124), 10), record)
                else:
                    with self.assertRaises(HTTPException):
                        require_record_access(session, SimpleNamespace(id=1, oid=124), 10)
                self.assertIs(service.ds, ds)

    def test_worker_history_uses_bound_assistant_without_request_context(self):
        record = SimpleNamespace(id=10, create_by=1, chat_id=5, datasource=2, sql='SELECT * FROM orders')
        ds = SimpleNamespace(id=2, physicalDatasourceKey='source:second')
        assistant = object()
        for allowed in (True, False):
            session = MagicMock()
            session.get.side_effect = [record, SimpleNamespace(create_by=1, oid=124)]
            service = MagicMock()
            service._ensure_out_ds_instance.return_value = SimpleNamespace(ds_list=[ds])
            service._do_permission_check.return_value = SimpleNamespace(allowed=allowed)
            with patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', True), \
                 patch('apps.chat.task.record_access.RequestContext.get_request', side_effect=RuntimeError('no worker context')) as request, \
                 patch('apps.chat.task.llm.LLMService.__new__', return_value=service):
                if allowed:
                    self.assertIs(require_record_access(session, SimpleNamespace(id=1, oid=124), 10,
                                                       current_assistant=assistant), record)
                else:
                    with self.assertRaises(HTTPException):
                        require_record_access(session, SimpleNamespace(id=1, oid=124), 10, current_assistant=assistant)
                request.assert_not_called()
                self.assertIs(service.current_assistant, assistant)
                service._do_permission_check.assert_called_once()

    def test_stream_preserves_errors(self):
        def fail():
            raise ValueError('model rejected')
        with self.assertRaisesRegex(ValueError, 'model rejected'):
            list(bounded_stream(fail, time.monotonic()+1))
