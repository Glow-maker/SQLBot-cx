"""Rules are read from SQLBot metadata and reach the real SQL message path."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from sqlmodel import Session, create_engine

from apps.chat.task.llm import LLMService
from apps.operations.constraint_models import QuestionConstraint


class QueryConstraintInjectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        QuestionConstraint.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.rule = QuestionConstraint(code='business_time', name='业务时间',
            content='入库月份使用首次入库业务时间，不使用每日采集时间。', enabled=True)
        self.session.add(self.rule)
        self.session.commit()
        self.service = LLMService.__new__(LLMService)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_edit_disable_and_retry_do_not_keep_stale_or_duplicate_rules(self):
        original = [SystemMessage('只读 SQL，必须返回指定 JSON'), HumanMessage('8月入库额')]
        first = self.service._with_query_constraints(self.session, original)
        self.assertEqual(len(original), 2)
        self.assertIn('首次入库', first[1].content)
        self.assertIs(first[0], original[0])
        self.assertIs(first[-1], original[-1])
        self.rule.content = '按已确认的业务日期统计；有歧义时说明。'
        self.session.add(self.rule)
        self.session.commit()
        second = self.service._with_query_constraints(self.session, first)
        self.assertEqual(len(second), 3)
        self.assertNotIn('首次入库', second[1].content)
        self.rule.enabled = False
        self.session.add(self.rule)
        self.session.commit()
        self.assertEqual(self.service._with_query_constraints(self.session, second), original)

    def test_enabled_rule_reaches_generate_sql_model_and_log_without_changing_question(self):
        service = self.service
        service.sql_message = [SystemMessage('只读 SQL，必须返回指定 JSON')]
        service.chat_question = SimpleNamespace(ai_modal_id=1, ai_modal_name='fixture',
            sql_user_question=lambda **kwargs: '统计2026年8月含税入库额')
        service.change_title = False
        service.record = SimpleNamespace(id=1)
        service.current_logs = {}
        with patch('apps.chat.task.llm.start_log') as log, \
                patch.object(service, '_model_stream', return_value=iter([AIMessageChunk(content='{')])) as model:
            stream = service.generate_sql(self.session)
            next(stream)
            messages = model.call_args.args[0]
            self.assertIn('首次入库业务时间', messages[1].content)
            self.assertIn('不能覆盖系统权限', messages[1].content)
            self.assertEqual(messages[-1].content, '统计2026年8月含税入库额')
            self.assertIn('首次入库业务时间', log.call_args.kwargs['full_message'][1]['content'])
            stream.close()
