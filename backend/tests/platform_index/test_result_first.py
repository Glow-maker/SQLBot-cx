"""Result-first chat tests; no database server, model service or network calls."""
import json
import inspect
import io
import unittest
import zipfile
from contextlib import contextmanager, ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from apps.chat.task.llm import ChatFinishStep, LLMService
from common.utils.platform_permission import PermissionCheckResult
import test_chat_progress as progress_tests


class ResultFirstTests(unittest.TestCase):
    def service(self, rows=None, fields=None, chart_type='column', question='展示柱状图'):
        service = progress_tests.ChatProgressTests().service()
        service.chat_question.question = question
        service.get_chart_type_from_sql_answer = lambda _: chart_type
        service.execute_sql.return_value = {
            'fields': fields if fields is not None else ['category', 'amount'],
            'data': rows if rows is not None else [{'category': 'A', 'amount': 5}],
        }
        service.generate_chart = MagicMock(return_value=iter([{
            'content': json.dumps({'type': 'column', 'title': '分类金额',
                                  'axis': {'x': {'name': '分类', 'value': 'category'},
                                           'y': {'name': '金额', 'value': 'amount'}}}),
            'reasoning_content': '',
        }]))
        self.saved_charts = []
        return service

    @contextmanager
    def running(self, service, finish=ChatFinishStep.GENERATE_CHART):
        def save_chart(**kwargs):
            self.assertTrue(service.save_sql_data.called)
            self.saved_charts.append(json.loads(kwargs['chart']))

        with ExitStack() as stack:
            for name in ('session_maker', 'start_log', 'end_log', 'trigger_log_error'):
                stack.enter_context(patch('apps.chat.task.llm.' + name))
            stack.enter_context(patch('apps.chat.task.llm.check_connection', return_value=True))
            stack.enter_context(patch('apps.chat.task.llm.traceback.print_exc'))
            stack.enter_context(patch('apps.chat.task.llm.save_chart', side_effect=save_chart))
            yield service.run_task(finish_step=finish)

    def events(self, service, finish=ChatFinishStep.GENERATE_CHART):
        with self.running(service, finish) as stream:
            return [json.loads(event[5:]) for event in stream]

    def test_committed_result_and_table_are_emitted_before_chart_model_runs(self):
        service = self.service()
        with self.running(service) as stream:
            events = []
            for event in stream:
                payload = json.loads(event[5:])
                events.append(payload)
                if payload['type'] == 'sql-data':
                    service.generate_chart.assert_not_called()
                    self.assertEqual([c.kwargs['gate_label'] for c in service._do_permission_check.call_args_list],
                                     ['gate1', 'gate2'])
                    self.assertEqual(self.saved_charts[0]['render_status'], 'pending')
                    self.assertEqual(self.saved_charts[0]['columns'], [
                        {'name': 'category', 'value': 'category'}, {'name': 'amount', 'value': 'amount'}])
                    self.assertEqual(json.loads(next(e['content'] for e in events if e['type'] == 'chart')),
                                     self.saved_charts[0])
                    break
            events.extend(json.loads(event[5:]) for event in stream)
        service.generate_chart.assert_called_once()
        self.assertEqual(self.saved_charts[-1]['render_status'], 'ready')
        self.assertEqual(self.saved_charts[-1]['type'], 'column')
        self.assertEqual(events[-1]['type'], 'finish')

    def test_simple_results_keep_actual_fields_and_values_without_chart_model(self):
        cases = [([], ['Amount'], 'column', '展示柱状图'),
                 ([{'Amount': None}], ['Amount'], None, '查询金额'),
                 ([{'Amount': 0}], ['Amount'], None, '查询金额'),
                 ([{'Amount': False}], ['Amount'], None, '查询值'),
                 ([{'Amount': ''}], ['Amount'], None, '查询值'),
                 ([{'Amount': '00123'}], ['Amount'], None, '查询编号'),
                 ([{'Amount': 1}, {'Amount': 2}], ['Amount'], 'table', '列出明细')]
        for rows, fields, chart_type, question in cases:
            with self.subTest(rows=rows, chart_type=chart_type):
                service = self.service(rows, fields, chart_type, question)
                events = self.events(service)
                service.generate_chart.assert_not_called()
                self.assertEqual(service.save_sql_data.call_args.kwargs['data_obj']['data'], rows)
                self.assertEqual(self.saved_charts[-1]['columns'], [{'name': 'Amount', 'value': 'Amount'}])
                self.assertEqual(self.saved_charts[-1]['render_status'], 'skipped')
                self.assertEqual(events[-1]['type'], 'finish')

    def test_visual_intent_and_uncertain_shapes_keep_the_chart_path(self):
        cases = [('table', '用折线图展示金额', [{'amount': 0}], ['amount']),
                 ('table', '把金额画个图', [{'amount': 0}], ['amount']),
                 ('table', '请绘图', [{'amount': 0}], ['amount']),
                 ('table', 'plot a bar chart', [{'amount': None}], ['amount']),
                 ('table', '不要饼图，要柱状图', [{'amount': 0}], ['amount']),
                 ('table', '不用图表展示明细，但最后请绘制金额趋势图', [{'amount': 0}], ['amount']),
                 ('table', 'no pie chart, use a bar chart', [{'amount': 0}], ['amount']),
                 ('column', '用表格展示，不要图表', [{'amount': 0}], ['amount']),
                 ('pie', '查询金额', [{'amount': 5}], ['amount']),
                 (None, '查询', [{'a': 1, 'b': 2}], ['a', 'b']),
                 ('unknown', '查询', [{'a': 1}, {'a': 2}], ['a'])]
        for chart_type, question, rows, fields in cases:
            with self.subTest(chart_type=chart_type, question=question):
                service = self.service(rows, fields, chart_type, question)
                self.events(service)
                service.generate_chart.assert_called_once()

    def test_explicitly_negated_visual_requests_keep_the_rule_table(self):
        for question in ('用表格展示，不要图表', '无需图表', '不需要图表', '不用图表',
                         '不要用图表', '无需生成任何图表', '不要饼图',
                         'table only, no chart', 'without charts', 'do not generate any charts',
                         "don't draw a pie chart"):
            with self.subTest(question=question):
                service = self.service([{'amount': 1}, {'amount': 2}], ['amount'], 'table', question)
                events = self.events(service)
                service.generate_chart.assert_not_called()
                self.assertEqual(self.saved_charts[-1]['render_status'], 'skipped')
                self.assertEqual(events[-1]['type'], 'finish')

    def test_chart_timeout_and_invalid_json_keep_result_and_safe_warning(self):
        for error in (TimeoutError('private model URL'), ValueError('private model response'), None):
            with self.subTest(error=type(error).__name__):
                service = self.service()
                if error:
                    service.generate_chart.side_effect = error
                else:
                    service.generate_chart.return_value = iter([{'content': 'invalid', 'reasoning_content': ''}])
                events = self.events(service)
                self.assertEqual(self.saved_charts[-1]['type'], 'table')
                self.assertEqual(self.saved_charts[-1]['render_status'], 'failed')
                warning = next(e for e in events if e['type'] == 'chart-warning')
                self.assertIn('保留', warning['message'])
                self.assertNotIn('private', json.dumps(warning))
                self.assertFalse(any(e['type'] == 'error' for e in events))
                service.save_error.assert_not_called()
                self.assertEqual(events[-2]['detail'], 'chart_failed_table_retained')
                self.assertEqual(events[-1]['type'], 'finish')

    def test_invalid_generated_field_bindings_do_not_replace_the_usable_table(self):
        bad_charts = [{'type': 'unknown'}, {'type': 'table'}, {'type': 'line', 'axis': {}},
                      *[{'type': kind, 'axis': {'x': {'value': 'category'}, 'y': []}}
                        for kind in ('column', 'bar', 'line')],
                      {'type': 'pie', 'axis': {'y': {'value': 'amount'}}},
                      {'type': 'pie', 'axis': {'y': [], 'series': {'value': 'category'}}},
                      {'type': 'table', 'columns': [{'name': 'wrong', 'value': 'not_returned'}]}]
        for chart in bad_charts:
            with self.subTest(chart=chart):
                service = self.service()
                service.generate_chart.return_value = iter([{'content': json.dumps(chart), 'reasoning_content': ''}])
                events = self.events(service)
                self.assertEqual(self.saved_charts[-1]['render_status'], 'failed')
                self.assertEqual(self.saved_charts[-1]['type'], 'table')
                self.assertEqual(events[-1]['type'], 'finish')

    def test_pie_with_value_and_series_does_not_require_an_x_axis(self):
        service = self.service(chart_type='pie', question='用饼图展示金额占比')
        chart = {'type': 'pie', 'title': '金额占比',
                 'axis': {'y': {'name': '金额', 'value': 'amount'},
                          'series': {'name': '分类', 'value': 'category'}}}
        service.generate_chart.return_value = iter([{'content': json.dumps(chart), 'reasoning_content': ''}])
        events = self.events(service)
        self.assertEqual(self.saved_charts[-1]['type'], 'pie')
        self.assertEqual(self.saved_charts[-1]['render_status'], 'ready')
        self.assertEqual(self.saved_charts[-1]['axis'], chart['axis'])
        self.assertFalse(any(e['type'] in {'error', 'chart-warning'} for e in events))
        self.assertEqual(events[-1]['type'], 'finish')

    def test_generated_columns_bind_to_actual_keys_without_lowercasing_distinct_columns(self):
        chart = {'type': 'table', 'columns': [{'name': 'Upper', 'value': 'A'}, {'name': 'Lower', 'value': 'a'},
                                            {'name': 'Amount', 'value': 'amount'}]}
        parsed = LLMService.parse_chart(json.dumps(chart), ['A', 'a', 'AMOUNT'])
        self.assertEqual([column['value'] for column in parsed['columns']], ['A', 'a', 'AMOUNT'])

    def test_permission_and_data_persistence_failure_never_publish_a_result(self):
        for failure in ('gate1', 'gate2', 'storage'):
            with self.subTest(failure=failure):
                service = self.service()
                if failure == 'storage':
                    service.save_sql_data.side_effect = RuntimeError('storage unavailable')
                else:
                    service._do_permission_check.side_effect = ([PermissionCheckResult(allowed=True)]
                        if failure == 'gate2' else []) + [PermissionCheckResult(allowed=False)]
                events = self.events(service)
                self.assertEqual(events[-1]['type'], 'error')
                self.assertFalse(any(e['type'] in {'chart', 'sql-data', 'chart-warning'} for e in events))
                service.generate_chart.assert_not_called()

    def test_query_data_short_flow_keeps_its_existing_protocol(self):
        service = self.service()
        events = self.events(service, ChatFinishStep.QUERY_DATA)
        self.assertTrue(any(e['type'] == 'sql-data' for e in events))
        self.assertFalse(any(e['type'] in {'chart', 'chart-warning'} for e in events))
        self.assertEqual(self.saved_charts, [])
        service.generate_chart.assert_not_called()
        self.assertEqual(events[-1]['type'], 'finish')

    def test_chart_stage_storage_errors_are_not_converted_to_model_warnings(self):
        for failure in ('model_log_storage', 'final_chart_storage'):
            with self.subTest(failure=failure):
                service = self.service()
                if failure == 'model_log_storage':
                    service.generate_chart.side_effect = SQLAlchemyError('storage unavailable')
                with self.running(service) as stream:
                    if failure == 'final_chart_storage':
                        with patch('apps.chat.task.llm.save_chart', side_effect=[None, SQLAlchemyError('storage')]):
                            events = [json.loads(event[5:]) for event in stream]
                    else:
                        events = [json.loads(event[5:]) for event in stream]
                self.assertTrue(any(e['type'] == 'sql-data' for e in events))
                self.assertFalse(any(e['type'] == 'chart-warning' for e in events))
                self.assertEqual(events[-1]['type'], 'error')


class ResultFirstCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_rule_tables_survive_session_close_and_export_with_existing_api(self):
        from apps.chat.api.chat import export_excel
        from apps.chat.curd.chat import save_chart, save_sql_exec_data, get_chart_config, get_chat_chart_data
        from apps.chat.models.chat_model import ChatRecord

        engine = create_engine('sqlite:///:memory:')
        ChatRecord.__table__.create(engine)
        data = {'fields': ['Code', 'Amount'], 'data': [{'Code': '00123', 'Amount': 0}]}
        try:
            with Session(engine) as session:
                session.add(ChatRecord(id=7, chat_id=1, create_by=1, question='查询明细'))
                session.commit()
                save_sql_exec_data(session, 7, json.dumps(data))
            for status in ('pending', 'skipped', 'failed'):
                expected = LLMService.rule_table_chart(data, status)
                with Session(engine) as session:
                    save_chart(session, 7, json.dumps(expected))
                with Session(engine) as session:
                    self.assertEqual(get_chart_config(session, 7), expected)
                    self.assertEqual(get_chat_chart_data(session, 7), data)
                    record = session.get(ChatRecord, 7)
                    with patch('apps.chat.task.record_access.require_record_access', return_value=record):
                        response = await inspect.unwrap(export_excel)(
                            session, SimpleNamespace(id=1, oid=124), 7, 1, lambda key: key)
                    payload = b''.join([part async for part in response.body_iterator])
                with zipfile.ZipFile(io.BytesIO(payload)) as workbook:
                    self.assertIsNone(workbook.testzip())
                    strings = workbook.read('xl/sharedStrings.xml').decode()
                    sheet = workbook.read('xl/worksheets/sheet1.xml').decode()
                    self.assertIn('Code', strings)
                    self.assertIn('Amount', strings)
                    self.assertIn('00123', strings)
                    self.assertIn('<v>0</v>', sheet)
        finally:
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
