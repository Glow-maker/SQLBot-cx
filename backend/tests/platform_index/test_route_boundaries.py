"""Exercise actual LLM routing rejection branches with a deterministic fake LLM."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.chat.task.llm import LLMService


class RouteBoundaryTests(unittest.TestCase):
    def service(self, candidates, answer):
        service = LLMService.__new__(LLMService)
        service.chat_question = SimpleNamespace(question="订单金额", ai_modal_id=1, ai_modal_name="mock")
        service.record = SimpleNamespace(id=1)
        service.current_logs = {}
        service.current_assistant = object()
        service.out_ds_instance = SimpleNamespace(metadata_load_ms=0)
        service.llm = MagicMock()
        service._logical_table_entries = lambda: candidates or [self.entry(1)]
        service._recall_logical_table_entries = lambda *_: candidates
        return service

    @staticmethod
    def entry(i, physical="pg|host1|5432"):
        return dict(datasourceId=i, tableId=i, schemaName="schema_"+str(i), name="orders",
                    qualifiedName="schema_"+str(i)+".orders", physicalDatasourceKey=physical,
                    fields=[dict(name="amount", comment="金额")])

    def reject(self, entries, answer, reason):
        service = self.service(entries, answer)
        with patch("apps.chat.task.llm.start_log"), patch("apps.chat.task.llm.end_log"), \
             patch("apps.chat.task.llm.process_stream", return_value=iter([{"content": json.dumps(answer)}])):
            with self.assertRaises(Exception) as caught:
                list(service.route_logical_datasource(None))
        self.assertIn(reason, str(caught.exception))
        return service

    def test_no_candidates_never_calls_llm(self):
        service = self.reject([], {}, "index_not_ready")
        service.llm.stream.assert_not_called()

    def test_llm_cannot_select_outside_candidates(self):
        self.reject([self.entry(1)], {"success": True, "tables": [self.entry(2)]}, "invalid_route_selection")

    def test_ambiguous_names_are_rejected_even_if_model_picks_one(self):
        entries = [dict(self.entry(i, str(i)), schemaName='public', qualifiedName='public.orders', datasourceName='source'+str(i)) for i in (1,2)]
        self.reject(entries, {'success':True,'tables':[entries[0]]}, 'ambiguous_datasource')

    def test_followup_source_requires_current_record_authorization(self):
        entries = [dict(self.entry(1), datasourceName='source1')]
        service = self.service(entries, {})
        service.generate_sql_logs = [SimpleNamespace(pid=7)]
        service.current_user = SimpleNamespace(id=1)
        previous = SimpleNamespace(datasource=1, sql='select amount from orders', question='source1订单金额')
        with patch('apps.chat.task.record_access.require_record_access', return_value=previous) as access:
            question = service._logical_question_with_history(None, entries)
        access.assert_called_once_with(None, service.current_user, 7, current_assistant=service.current_assistant)
        self.assertIn('当前续问沿用数据源：source1', question)
        self.assertIn(service.chat_question.question, question)

    def test_revoked_history_cannot_disambiguate_or_enter_question(self):
        service = self.service([], {})
        service.generate_sql_logs = [SimpleNamespace(pid=7)]
        service.current_user = SimpleNamespace(id=1)
        with patch('apps.chat.task.record_access.require_record_access', side_effect=PermissionError):
            self.assertEqual(service._logical_question_with_history(None, []), service.chat_question.question)

    def test_explicit_new_source_does_not_reuse_history(self):
        entries = [dict(self.entry(2), datasourceName='source2')]
        service = self.service(entries, {})
        service.chat_question.question = 'source2订单金额'
        service.generate_sql_logs = [SimpleNamespace(pid=7)]
        with patch('apps.chat.task.record_access.require_record_access') as access:
            self.assertEqual(service._logical_question_with_history(None, entries), 'source2订单金额')
        access.assert_not_called()

    def test_cross_physical_source_still_rejected(self):
        entries = [self.entry(1), self.entry(2, "pg|host2|5432")]
        self.reject(entries, {"success": True, "tables": entries}, "cross_source_not_supported")

    def test_llm_can_reject_irrelevant_candidates(self):
        self.reject([self.entry(1)], {"success": False}, "no_relevant_table")
