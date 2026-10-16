"""Mock-only: execute the real chat generator, without databases/model services."""
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.chat.task.llm import LLMService, ChatFinishStep, SingleMessageError
from apps.chat.task.progress import ChatProgress
from common.utils.platform_permission import PermissionCheckResult, denied_tables_to_sse_payload


class ChatProgressTests(unittest.TestCase):
    def test_actual_permission_client_failures_never_execute_query(self):
        import requests
        from common.utils import platform_permission as client
        responses = [requests.Timeout('test timeout'), MagicMock(status_code=503, text='unavailable'),
            MagicMock(status_code=200, json=MagicMock(side_effect=ValueError('invalid JSON'))),
            MagicMock(status_code=200, json=lambda: {'code':0,'data':{'allowed':'false','datasourceId':1}}),
            MagicMock(status_code=200, json=lambda: {'code':0,'data':{'allowed':True,'datasourceId':2}})]
        for response in responses:
            with patch.object(client, 'is_permission_check_enabled', return_value=True), \
                 patch.object(client.settings, 'PLATFORM_DATASOURCE_BASE_URL', 'http://permission.test'), \
                 patch.object(client.requests, 'post', side_effect=response if isinstance(response, Exception) else None,
                              return_value=response):
                denied = client.check_table_permissions(authorization_header='Bearer test', tenant_id=124,
                    datasource_id=1, sql='SELECT * FROM orders', tables=[{'name':'orders'}])
            self.assertFalse(denied.allowed)
            service = self.service()
            service._do_permission_check.return_value = denied
            self.run_service(service)
            service.execute_sql.assert_not_called()

    def service(self):
        s = LLMService.__new__(LLMService)
        s.ds = SimpleNamespace(id=1)
        s.record = SimpleNamespace(id=7, regenerate_record_id=None, question='test')
        s.get_record = lambda: s.record
        s.chat_question = SimpleNamespace(question='test')
        s.current_logs = {}
        s.current_assistant = SimpleNamespace(type=99)
        s.change_title = False
        s.out_ds_instance = SimpleNamespace(get_db_schema=lambda *a, **k: 'schema')
        for name in ('filter_terminology_template', 'filter_training_template', 'filter_custom_prompts',
                     'init_messages', 'validate_history_ds', '_validate_logical_sql_route', 'save_sql_data',
                     'save_error', 'finish'):
            setattr(s, name, MagicMock())
        s.get_chart_type_from_sql_answer = lambda _: 'table'
        s.check_sql = MagicMock(return_value=('SELECT 1', ['a']))
        s.check_save_sql = MagicMock(return_value='SELECT 1')
        s.generate_sql = MagicMock(return_value=iter([
            {'_progress': 'waiting_model'}, {'content': '', 'reasoning_content': ''},
            {'content': 'SELECT 1', 'reasoning_content': ''}]))
        s._do_permission_check = MagicMock(return_value=PermissionCheckResult(allowed=True))
        s.execute_sql = MagicMock(return_value={'data': [], 'fields': []})
        s.generate_chart = MagicMock(return_value=iter([{'content': '{}', 'reasoning_content': ''}]))
        s.check_save_chart = MagicMock(return_value={'type': 'table'})
        return s

    def run_service(self, s, finish=ChatFinishStep.GENERATE_CHART, connected=True):
        with ExitStack() as stack:
            for name in ('session_maker', 'start_log', 'end_log', 'save_chart'):
                stack.enter_context(patch('apps.chat.task.llm.' + name))
            stack.enter_context(patch('apps.chat.task.llm.check_connection', return_value=connected))
            stack.enter_context(patch('apps.chat.task.llm.traceback.print_exc'))
            return [json.loads(x[5:]) for x in s.run_task(finish_step=finish)]

    def test_success_orders_gates_before_execution_and_resolves_all_stages(self):
        s = self.service()
        events = self.run_service(s)
        p = [e for e in events if e['type'] == 'progress']
        self.assertEqual(events[-1]['type'], 'finish')
        terminal = {e['stage']: e['status'] for e in p}
        self.assertEqual(terminal, dict(route='skipped', connect='completed', generate='completed',
                                        permission='completed', query='completed', result='completed'))
        self.assertEqual([c.kwargs['gate_label'] for c in s._do_permission_check.call_args_list], ['gate1', 'gate2'])
        self.assertEqual([e['detail'] for e in p if e['stage'] == 'generate' and e['status'] == 'running'],
                         ['preparing_context', 'waiting_model', 'receiving_model'])
        self.assertLess(next(i for i,e in enumerate(p) if e['stage']=='permission' and e['status']=='completed'),
                        next(i for i,e in enumerate(p) if e['stage']=='query'))
        s.execute_sql.assert_called_once()

    def test_both_gate_refusals_and_auth_expiry_stop_before_query(self):
        for gate, code in [(1, None), (2, None), (1, 401)]:
            with self.subTest(gate=gate, code=code):
                s = self.service()
                denied = PermissionCheckResult(allowed=False, reason='permission_service_biz_error' if code else None,
                                                raw_payload={'code': code} if code else None)
                s._do_permission_check.side_effect = [PermissionCheckResult(allowed=True)] * (gate - 1) + [denied]
                events = self.run_service(s)
                self.assertEqual(events[-1]['type'], 'error')
                self.assertEqual(events[-2]['stage'], 'permission')
                self.assertEqual(events[-2]['status'], 'failed')
                self.assertFalse(any(e.get('stage') == 'query' for e in events))
                s.execute_sql.assert_not_called()
                payload = json.loads(events[-1]['content'])
                self.assertEqual(payload['errorCode'], 'authentication_expired' if code else 'table_permission_denied')

    def test_connection_model_and_parser_failure_report_actual_stage(self):
        for stage in ['connect', 'generate', 'permission']:
            with self.subTest(stage=stage):
                s = self.service()
                if stage == 'generate':
                    s.generate_sql.side_effect = RuntimeError('model unavailable')
                if stage == 'permission':
                    s.check_sql.side_effect = SingleMessageError('{"type":"cross_source_not_supported"}')
                events = self.run_service(s, connected=stage != 'connect')
                self.assertEqual(events[-2]['stage'], stage)
                self.assertEqual(events[-2]['status'], 'failed')
                s.execute_sql.assert_not_called()

    def test_short_flows_skip_unrequested_work(self):
        for finish in [ChatFinishStep.GENERATE_SQL, ChatFinishStep.QUERY_DATA]:
            s = self.service()
            events = self.run_service(s, finish)
            last = {e['stage']: e['status'] for e in events if e['type'] == 'progress'}
            self.assertEqual(last['result'], 'skipped')
            self.assertEqual(events[-1]['type'], 'finish')
            if finish == ChatFinishStep.GENERATE_SQL:
                self.assertEqual(last['permission'], 'skipped')
                self.assertEqual(last['query'], 'skipped')
                s.execute_sql.assert_not_called()

    def test_tracker_uses_monotonic_time_and_ignores_terminal_repeats(self):
        now = [1.0]
        p = ChatProgress(clock=lambda: now[0])
        p.update('generate')
        now[0] = 3.5
        self.assertEqual(json.loads(p.update('generate', 'completed')[0][5:])['elapsedMs'], 2500)
        self.assertEqual(p.update('generate'), [])
        self.assertEqual(ChatProgress(False).update('route'), [])

    def test_route_details_preserve_total_and_measure_each_real_step(self):
        now = [10.0]
        p = ChatProgress(clock=lambda: now[0])
        p.update('route')
        p.update_detail('route', 'metadata')
        now[0] = 10.3
        metadata = json.loads(p.update_detail('route', 'metadata', 'completed')[0][5:])
        self.assertEqual(metadata['detailElapsedMs'], 300)
        self.assertEqual(metadata['status'], 'running')
        p.update_detail('route', 'recall')
        now[0] = 10.8
        recall = json.loads(p.update_detail('route', 'recall', 'completed')[0][5:])
        self.assertEqual(recall['detailElapsedMs'], 500)
        self.assertEqual(recall['elapsedMs'], 800)
        self.assertEqual(p.update_detail('route', 'recall'), [])
        p.update_detail('route', 'selection')
        now[0] = 12.0
        failure = [json.loads(e[5:]) for e in p.fail()]
        self.assertEqual(failure[0]['detailStage'], 'selection')
        self.assertEqual(failure[0]['detailStatus'], 'failed')
        self.assertEqual(failure[0]['detailElapsedMs'], 1200)
        self.assertEqual(failure[-1]['status'], 'failed')
        self.assertEqual(failure[-1]['elapsedMs'], 2000)
        self.assertEqual(p.update_detail('route', 'selection'), [])
        self.assertEqual(ChatProgress(False).update_detail('route', 'metadata'), [])

    def test_catalog_failure_reports_detail_and_does_not_recall_or_query(self):
        s = self.service()
        s.ds = None
        s._logical_datasource_enabled = lambda: True
        s._logical_table_entries = MagicMock(side_effect=RuntimeError('catalog unavailable'))
        s._recall_logical_table_entries = MagicMock()
        events = self.run_service(s)
        details = [e for e in events if e.get('detailStage')]
        self.assertEqual([(e['detailStage'], e['detailStatus']) for e in details],
                         [('metadata', 'running'), ('metadata', 'failed')])
        self.assertEqual(events[-2]['stage'], 'route')
        self.assertEqual(events[-2]['status'], 'failed')
        s._recall_logical_table_entries.assert_not_called()
        s.execute_sql.assert_not_called()

    def test_legacy_datasource_stream_is_unchanged_and_has_no_route_details(self):
        s = self.service()
        s.ds = None
        s._logical_datasource_enabled = lambda: False

        def select_datasource(_session):
            s.ds = SimpleNamespace(id=1, name='Legacy source', type='postgresql', type_name='PostgreSQL')
            yield {'content': '{"id":1}', 'reasoning_content': ''}

        s.select_datasource = select_datasource
        events = self.run_service(s)
        self.assertEqual(events[-1]['type'], 'finish')
        self.assertFalse(any(e.get('detailStage') for e in events))
        self.assertEqual([e['content'] for e in events if e['type'] == 'datasource-result'], ['{"id":1}'])
        self.assertEqual([e['id'] for e in events if e['type'] == 'datasource'], [1])
        s.execute_sql.assert_called_once()

    def test_http_and_business_401_classified_without_changing_denial(self):
        for result in [PermissionCheckResult(False, reason='permission_service_status_401'),
                       PermissionCheckResult(False, raw_payload={'code': 401}),
                       PermissionCheckResult(False, raw_payload={'groups': [
                           {'allowed': False, 'reason': 'permission_service_biz_error: None', 'raw': {'code': 401}}
                       ]})]:
            self.assertFalse(result.allowed)
            self.assertEqual(denied_tables_to_sse_payload(result, 'gate1')['errorCode'], 'authentication_expired')
