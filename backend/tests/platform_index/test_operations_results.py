import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sqlbot_xpack
from pydantic import ValidationError
from sqlmodel import Session, create_engine

from apps.chat.task.llm import LLMService
from apps.operations.api import Filters, interactions, overview
from apps.operations.capture import Capture
from apps.operations.models import Feedback, Interaction


class QueryResultTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        for model in (Interaction, Feedback):
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.admin = SimpleNamespace(auth_source='local', oid=1, id=1, isAdmin=True)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def capture(self, kind='question', tenant=124):
        item = Interaction(scope='platform', auth_source='platform', tenant_id=tenant, user_id=5, kind=kind)
        self.session.add(item)
        self.session.commit()
        return Capture(item.id, self.engine, time.perf_counter())

    def saved(self, capture):
        self.session.expire_all()
        return self.session.get(Interaction, capture.id)

    def execute(self, capture, rows):
        service = LLMService.__new__(LLMService)
        service.record = SimpleNamespace(id=18)
        service.ds = SimpleNamespace(id=1)
        service.operations_capture = capture
        result = {'fields': ['amount'], 'data': rows}
        with patch('apps.chat.task.llm.exec_sql', return_value=result), \
                patch('apps.chat.task.llm.SQLBotLogUtil.info'):
            returned = service.execute_sql('SELECT amount FROM orders')
        self.assertIs(returned, result)
        self.assertIs(returned['data'], rows)
        return service

    def test_query_execution_distinguishes_zero_rows_null_cells_and_real_zero(self):
        cases = [([], 'empty_result'), ([{'amount': None}], 'all_null_result'),
                 ([{'amount': None, 'count': None}, {'amount': None, 'count': None}], 'all_null_result'),
                 ([{'amount': 0}], 'rows_returned'), ([{'amount': False}], 'rows_returned'),
                 ([{'amount': ''}], 'rows_returned'),
                 ([{'amount': None}, {'amount': 10}], 'rows_returned')]
        for rows, expected in cases:
            with self.subTest(rows=rows):
                capture = self.capture()
                self.execute(capture, rows)
                capture.finish()
                item = self.saved(capture)
                self.assertEqual((item.status, item.outcome), ('success', expected))

    def test_result_values_are_not_retained_by_capture_or_operational_snapshot(self):
        capture = self.capture()
        self.execute(capture, [{'amount': 'private-business-value'}])
        capture.finish()
        self.assertNotIn('private-business-value', str(vars(capture)))
        snapshot = self.saved(capture).model_dump()
        self.assertNotIn('private-business-value', json.dumps(snapshot, default=str))
        self.assertNotIn('data', snapshot)

    def test_fatal_failure_after_execution_still_takes_priority_over_query_result(self):
        for error, status in [(ValueError('persistence failed'), 'error'), (TimeoutError('deadline'), 'timeout')]:
            capture = self.capture()
            self.execute(capture, [])
            capture.failure(error)
            capture.finish()
            item = self.saved(capture)
            self.assertEqual(item.status, status)
            self.assertNotEqual(item.outcome, 'empty_result')

    def test_chart_fallback_preserves_query_outcome_and_failed_stage_without_private_text(self):
        for error, status in [(ValueError('private chart response'), 'error'),
                              (TimeoutError('private model address'), 'timeout')]:
            with self.subTest(error=type(error).__name__):
                capture = self.capture()
                self.execute(capture, [{'amount': 0}])
                capture.model('GENERATE_CHART', 1200, 100, {'total_tokens': 8}, 'completed', None)
                capture.chart_failure(error, 1201)
                capture.finish()
                item = self.saved(capture)
                self.assertEqual((item.status, item.outcome), ('success', 'rows_returned'))
                self.assertIsNone(item.error_summary)
                self.assertEqual(item.total_tokens, 8)
                self.assertEqual(len(item.stages_json), 1)
                self.assertEqual(item.stages_json[0]['status'], status)
                self.assertEqual(item.stages_json[0]['error_class'], type(error).__name__)
                self.assertNotIn('private', json.dumps(item.model_dump(), default=str))

    def test_chart_failure_before_model_output_has_a_bounded_stage(self):
        capture = self.capture()
        self.execute(capture, [{'amount': None}])
        capture.chart_failure(ValueError('private schema detail'), 12.34)
        capture.finish()
        item = self.saved(capture)
        self.assertEqual((item.status, item.outcome), ('success', 'all_null_result'))
        self.assertEqual(item.stages_json, [{'stage': 'GENERATE_CHART', 'status': 'error',
                                           'elapsedMs': 12.3, 'error_class': 'ValueError'}])
        self.assertIsNone(item.total_tokens)

    def test_non_query_and_unmeasured_result_are_not_inferred(self):
        for kind in ('analysis', 'predict', 'recommend'):
            capture = self.capture(kind=kind)
            capture.query_result([])
            capture.finish()
            self.assertEqual(self.saved(capture).outcome, 'completed')
        for unknown in (None, {}, [{}]):
            capture = self.capture()
            capture.query_result(unknown)
            capture.finish()
            self.assertEqual(self.saved(capture).outcome, 'completed')
        capture = self.capture()
        capture.finish()
        self.assertEqual(self.saved(capture).outcome, 'completed')

    def test_retry_results_are_classified(self):
        capture = self.capture(kind='retry')
        self.execute(capture, [])
        capture.finish()
        self.assertEqual(self.saved(capture).outcome, 'empty_result')

    def test_overview_and_list_filter_match_captured_results_without_guessing_old_rows(self):
        for rows in ([], [{'amount': None}], [{'amount': 0}]):
            capture = self.capture()
            self.execute(capture, rows)
            capture.finish()
        old = self.capture()
        old.finish()
        failed = self.capture()
        failed.query_result([])
        failed.failure(ValueError('chart failed'))
        failed.finish()
        foreign = self.capture(tenant=125)
        foreign.query_result([])
        foreign.finish()
        filters = Filters(scope='platform', tenant_id=124)
        result = overview(self.session, self.admin, filters)
        self.assertEqual(result['success'], 4)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['outcome_counts'], {'empty_result': 1, 'all_null_result': 1, 'rows_returned': 1})
        self.assertEqual((result['empty_results'], result['all_null_results']), (1, 1))
        for outcome in ('empty_result', 'all_null_result', 'rows_returned'):
            selected = filters.model_copy(update={'outcome': outcome})
            page = interactions(self.session, self.admin, selected)
            self.assertEqual(page['total'], 1)
            self.assertEqual(page['items'][0]['outcome'], outcome)
            self.assertEqual(overview(self.session, self.admin, selected)['total'], 1)

    def test_result_filter_rejects_unknown_categories(self):
        with self.assertRaises(ValidationError):
            Filters(outcome='business_answer_correct')
