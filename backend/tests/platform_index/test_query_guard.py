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

    def _stored_check(self, check, entries=(), select_answer=None, catalog=None, catalog_error=None, route_messages=None):
        record = SimpleNamespace(id=10, create_by=1, chat_id=5, datasource=2, sql='SELECT * FROM orders',
                                 datasource_select_answer=select_answer)
        session = MagicMock()
        session.get.side_effect = [record, SimpleNamespace(create_by=1, oid=124)]
        session.query.return_value.filter.return_value.all.return_value = (
            [SimpleNamespace(messages=route_messages)] if route_messages is not None else [])
        service = MagicMock()
        if catalog_error:
            service._ensure_out_ds_instance.side_effect = catalog_error
        service._ensure_out_ds_instance.return_value = SimpleNamespace(ds_list=[SimpleNamespace(id=2)])
        service._logical_table_entries.return_value = list(entries)
        service._do_permission_check.return_value = check
        with patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', True), \
             patch('apps.chat.task.record_access.RequestContext.get_request', return_value=SimpleNamespace(state=SimpleNamespace(assistant=object()))), \
             patch('apps.chat.task.llm.LLMService.__new__', return_value=service), \
             patch('apps.system.crud.assistant.AssistantOutDs._physical_datasource_key', return_value='source:second'):
            try:
                return require_record_access(session, SimpleNamespace(id=1, oid=124), 10,
                                             check_candidates=False, catalog=catalog), service
            except HTTPException as e:
                return e, service

    @staticmethod
    def _route(*tables):
        import json
        return json.dumps({'content': '选表结果：' + json.dumps({'success': True, 'tables': list(tables)})})

    def test_stored_recheck_uses_the_records_own_route(self):
        import json
        orders = dict(datasourceId=2, tableId=7, name='orders', schemaName='ods')
        other = dict(datasourceId=2, tableId=8, name='orders', schemaName='public')
        route = self._route({'datasourceId': 2, 'tableId': 7, 'name': 'orders'})
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), [orders, other], route)
        self.assertEqual(result.id, 10)
        self.assertEqual(service.logical_route_tables, [orders])
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), [orders], 'not json')
        self.assertEqual(service.logical_route_tables, [])
        aliased = json.dumps({'content': json.dumps({'success': True, 'tables': [{'datasource_id': 2, 'id': 7, 'name': 'orders'}]})})
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), [orders, other], aliased)
        self.assertEqual(service.logical_route_tables, [orders])

    def test_revoked_routed_table_cannot_resolve_to_a_same_named_granted_table(self):
        # Routed to ods.orders; the stored SQL says only "orders". After ods.orders is revoked,
        # public.orders (still granted) must not let the old ods rows through.
        public = dict(datasourceId=2, tableId=8, name='orders', schemaName='public')
        route = self._route({'datasourceId': 2, 'tableId': 7, 'name': 'orders'})
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), [public], route)
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(result.status_code, 403)
        service._do_permission_check.assert_not_called()
        # One of several routed tables gone: the result is withheld too.
        orders = dict(datasourceId=2, tableId=7, name='orders', schemaName='ods')
        both = self._route({'datasourceId': 2, 'tableId': 7}, {'datasourceId': 2, 'tableId': 9, 'name': 'revoked'})
        result, _ = self._stored_check(SimpleNamespace(allowed=True, reason=None), [orders, public], both)
        self.assertEqual(result.status_code, 403)

    def test_routed_table_must_keep_its_name(self):
        import json
        orders = dict(datasourceId=2, tableId=7, name='orders', schemaName='ods', fields=[dict(name='amount')])
        route = self._route({'datasourceId': 2, 'tableId': 7})
        card = dict(orders)
        messages = [dict(type='human', content=json.dumps(dict(tables=[card, dict(datasourceId=2, tableId=30, name='gone')])))]
        # Other candidates and fields may drift; only the routed table matters.
        result, _ = self._stored_check(SimpleNamespace(allowed=True, reason=None), [dict(orders, fields=[])], route, route_messages=messages)
        self.assertEqual(result.id, 10)
        result, _ = self._stored_check(SimpleNamespace(allowed=True, reason=None), [dict(orders, name='ORDERS')], route, route_messages=messages)
        self.assertEqual(result.id, 10)  # case only
        # Configuring db_schema or backfilling the registered schema relabels the same table; the SQL recheck decides.
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), [dict(orders, schemaName='public')], route, route_messages=messages)
        self.assertEqual(result.id, 10)
        service._do_permission_check.assert_called_once()
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), [dict(orders, name='orders_v2')], route, route_messages=messages)
        self.assertEqual(result.status_code, 403)
        service._do_permission_check.assert_not_called()
        # Without a readable route log the route answer's (possibly qualified) name is used.
        named = self._route({'datasourceId': 2, 'tableId': 7, 'name': 'ods.orders'})
        unreadable = [dict(type='human', content='x')]
        result, _ = self._stored_check(SimpleNamespace(allowed=True, reason=None), [orders], named, route_messages=unreadable)
        self.assertEqual(result.id, 10)
        result, _ = self._stored_check(SimpleNamespace(allowed=True, reason=None), [dict(orders, name='renamed')], named, route_messages=unreadable)
        self.assertEqual(result.status_code, 403)

    def test_records_without_sql_still_need_their_routed_tables(self):
        # e.g. denied at the first gate: no SQL, but the route answer and sql_answer name the routed table.
        orders = dict(datasourceId=2, tableId=7, name='orders', schemaName='ods')
        route = self._route({'datasourceId': 2, 'tableId': 7, 'name': 'orders'})
        for entries, expected in (([orders], None), ([], 403)):
            record = SimpleNamespace(id=10, create_by=1, chat_id=5, datasource=2, sql=None, datasource_select_answer=route)
            session = MagicMock()
            session.get.side_effect = [record, SimpleNamespace(create_by=1, oid=124)]
            session.query.return_value.filter.return_value.all.return_value = []
            service = MagicMock()
            service._ensure_out_ds_instance.return_value = SimpleNamespace(ds_list=[SimpleNamespace(id=2)])
            service._logical_table_entries.return_value = entries
            with patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', True), \
                 patch('apps.chat.task.record_access.RequestContext.get_request', return_value=SimpleNamespace(state=SimpleNamespace(assistant=object()))), \
                 patch('apps.chat.task.llm.LLMService.__new__', return_value=service):
                if expected:
                    with self.assertRaises(HTTPException) as caught:
                        require_record_access(session, SimpleNamespace(id=1, oid=124), 10, check_candidates=False)
                    self.assertEqual(caught.exception.status_code, expected)
                else:
                    self.assertIs(require_record_access(session, SimpleNamespace(id=1, oid=124), 10, check_candidates=False), record)
            service._do_permission_check.assert_not_called()

    @staticmethod
    def _resolver(entries, db_schema=None, database=None, routed=()):
        service = LLMService.__new__(LLMService)
        service.ds = SimpleNamespace(id=2, db_schema=db_schema, dataBase=database)
        service.logical_route_tables = list(routed)
        service._current_datasource_entries = lambda: list(entries)
        return service

    @staticmethod
    def _refs(sql, dialect='pg'):
        from common.utils.sql_parser import extract_table_refs
        return list(extract_table_refs(sql, dialect))

    def test_unqualified_name_only_resolves_in_the_connection_default_schema(self):
        ods = dict(datasourceId=2, tableId=7, name='orders', schemaName='ods')
        public = dict(datasourceId=2, tableId=8, name='orders', schemaName='public')
        # Granted only ods.orders; "FROM orders" would read public.orders on a search_path=public connection.
        resolved, unresolved = self._resolver([ods], db_schema='public')._resolve_table_inputs(self._refs('SELECT * FROM orders'))
        self.assertEqual(resolved, [])
        self.assertEqual(unresolved[0]['reason'], 'unqualified_table_outside_default_schema')
        # Even when the record was routed to ods.orders, the unqualified name is the default-schema table.
        resolved, _ = self._resolver([ods, public], db_schema='public', routed=[ods])._resolve_table_inputs(self._refs('SELECT * FROM orders'))
        self.assertEqual(resolved, [public])
        resolved, unresolved = self._resolver([ods, public], db_schema='public')._resolve_table_inputs(self._refs('SELECT * FROM ods.orders'))
        self.assertEqual((resolved, unresolved), ([ods], []))

    def test_default_schema_falls_back_like_tables_registered_without_schema(self):
        # MySQL: no db_schema, the connection database is the default schema.
        mysql = dict(datasourceId=2, tableId=7, name='orders', schemaName='bayuan')
        resolved, _ = self._resolver([mysql], database='bayuan')._resolve_table_inputs(self._refs('SELECT * FROM orders', 'mysql'))
        self.assertEqual(resolved, [mysql])
        # Registered without schema on a source without db_schema: the catalog names it after the database.
        blank = dict(datasourceId=2, tableId=9, name='visit', schemaName='hisdb')
        explicit = dict(datasourceId=2, tableId=10, name='ward', schemaName='ODS')
        service = self._resolver([blank, explicit], database='hisdb')
        self.assertEqual(service._resolve_table_inputs(self._refs('SELECT * FROM visit'))[0], [blank])
        self.assertEqual(service._resolve_table_inputs(self._refs('SELECT * FROM ward'))[1][0]['reason'],
                         'unqualified_table_outside_default_schema')
        self.assertEqual(service._resolve_table_inputs(self._refs('SELECT * FROM ods.ward'))[0], [explicit])

    def test_route_disambiguation_stays_inside_the_name_matches(self):
        first = dict(datasourceId=2, tableId=7, name='orders', schemaName='public')
        second = dict(datasourceId=3, tableId=8, name='orders', schemaName='public')
        other_schema = dict(datasourceId=2, tableId=9, name='orders', schemaName='ods')
        resolver = self._resolver([first, second, other_schema], db_schema='public', routed=[second])
        self.assertEqual(resolver._resolve_table_inputs(self._refs('SELECT * FROM orders'))[0], [second])
        resolver = self._resolver([first, second, other_schema], db_schema='public', routed=[other_schema])
        self.assertEqual(resolver._resolve_table_inputs(self._refs('SELECT * FROM orders'))[1][0]['reason'],
                         'ambiguous_table_reference')

    def test_stored_recheck_separates_service_failures_from_revocation(self):
        for reason, status in (('permission_service_timeout', 503), ('permission_service_status_401', 401),
                               ('user_lacks_table_permission', 403), ('table_not_registered_or_ambiguous', 403)):
            result, _ = self._stored_check(SimpleNamespace(allowed=False, reason=reason))
            self.assertIsInstance(result, HTTPException, reason)
            self.assertEqual(result.status_code, status, reason)
        result, _ = self._stored_check(None)
        self.assertEqual(result.status_code, 403)

    def test_catalog_failure_is_temporary_and_a_loaded_catalog_is_reused(self):
        result, _ = self._stored_check(SimpleNamespace(allowed=True, reason=None), catalog_error=RuntimeError('Read timed out'))
        self.assertEqual(result.status_code, 503)
        catalog = SimpleNamespace(ds_list=[SimpleNamespace(id=2)])
        result, service = self._stored_check(SimpleNamespace(allowed=True, reason=None), catalog=catalog)
        self.assertEqual(result.id, 10)
        self.assertIs(service.out_ds_instance, catalog)

    def test_usage_totals_skip_the_recheck_but_steps_keep_it(self):
        from apps.chat.curd.chat import get_chat_log_history
        for without_steps in (True, False):
            session = MagicMock()
            session.get.return_value = SimpleNamespace(id=10, create_by=1)
            with patch('apps.chat.task.record_access.require_record_access') as access, \
                 patch('apps.chat.task.record_access.require_record_owner') as owner:
                try:
                    get_chat_log_history(session, 10, SimpleNamespace(id=1, oid=124), without_steps)
                except Exception:
                    pass  # later formatting does not matter here
            self.assertEqual(access.called, not without_steps)
            self.assertEqual(owner.called, without_steps)  # usage still checks chat owner and tenant
            if not without_steps:
                self.assertNotIn('check_candidates', access.call_args.kwargs)

    def test_opening_a_chat_withholds_only_the_denied_record(self):
        from apps.chat.curd.chat import denied_records
        catalog = object()

        def check(session, user, record_id, **kwargs):
            self.assertIs(kwargs['catalog'], catalog)
            self.assertIs(kwargs['check_candidates'], False)
            if record_id == 2:
                raise HTTPException(403, '数据授权已变化，请使用当前可查询表重新提问')

        records = [SimpleNamespace(id=1, sql='SELECT 1'), SimpleNamespace(id=2, sql='SELECT 2'), SimpleNamespace(id=3, sql=None)]
        with patch('apps.chat.task.record_access.require_record_access', side_effect=check), \
             patch('apps.system.crud.assistant.AssistantOutDsFactory.get_instance', return_value=catalog) as load:
            self.assertEqual(denied_records(MagicMock(), SimpleNamespace(id=1), object(), records),
                             {2: '数据授权已变化，请使用当前可查询表重新提问'})
            load.assert_called_once()
        with patch('apps.chat.task.record_access.require_record_access') as access, \
             patch('apps.system.crud.assistant.AssistantOutDsFactory.get_instance') as load:
            self.assertEqual(denied_records(MagicMock(), SimpleNamespace(id=1), object(), [SimpleNamespace(id=3, sql=None)]), {})
            load.assert_not_called()  # nothing to recheck against the catalog
            self.assertIsNone(access.call_args.kwargs['catalog'])
        for status in (401, 503):
            with patch('apps.chat.task.record_access.require_record_access', side_effect=HTTPException(status, 'temporary')), \
                 patch('apps.system.crud.assistant.AssistantOutDsFactory.get_instance', return_value=catalog):
                with self.assertRaises(HTTPException):
                    denied_records(MagicMock(), SimpleNamespace(id=1), object(), [SimpleNamespace(id=1, sql='SELECT 1')])

    def test_analysis_and_predict_records_follow_their_base_record(self):
        import apps.chat.task.record_access as access_module
        user = SimpleNamespace(id=1, oid=124)
        objects = {('Chat', 5): SimpleNamespace(create_by=1, oid=124),
                   ('ChatRecord', 1): SimpleNamespace(id=1, create_by=1, chat_id=5, sql='SELECT * FROM orders'),
                   ('ChatRecord', 2): SimpleNamespace(id=2, create_by=1, chat_id=5, sql=None, analysis_record_id=1, predict_record_id=None),
                   ('ChatRecord', 3): SimpleNamespace(id=3, create_by=1, chat_id=5, sql=None, analysis_record_id=None, predict_record_id=None)}
        session = MagicMock()
        session.get.side_effect = lambda model, key: objects.get((model.__name__, key))
        with patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', True):
            with patch.object(access_module, 'require_record_access', side_effect=HTTPException(403, 'revoked')) as base:
                with self.assertRaises(HTTPException):
                    require_record_access(session, user, 2, check_candidates=False)
                self.assertEqual(base.call_args.args[2], 1)
                self.assertIs(base.call_args.kwargs['check_candidates'], False)
            with patch.object(access_module, 'require_record_access') as base:
                self.assertIs(require_record_access(session, user, 2, check_candidates=False), objects[('ChatRecord', 2)])
                self.assertIs(require_record_access(session, user, 3, check_candidates=False), objects[('ChatRecord', 3)])
                self.assertEqual(base.call_count, 1)  # only the analysis record follows a base record

    def test_api_record_paths_recheck_sql_without_candidate_drift(self):
        import ast, inspect
        import apps.chat.api.chat as api
        calls = [node for node in ast.walk(ast.parse(inspect.getsource(api)))
                 if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'require_record_access']
        self.assertEqual(len(calls), 3)  # recommend, analysis/predict, export
        for call in calls:
            flags = {k.arg: getattr(k.value, 'value', None) for k in call.keywords}
            self.assertIs(flags.get('check_candidates'), False)
        owners = [node for node in ast.walk(ast.parse(inspect.getsource(api)))
                  if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'require_record_owner']
        self.assertEqual(len(owners), 1)  # regenerate: the new SQL goes through the live gates
        self.assertEqual(api._error_text(HTTPException(403, '数据授权已变化')), '数据授权已变化')

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
