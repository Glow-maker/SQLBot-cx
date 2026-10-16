"""Recommendation workers must not retain request-owned ORM instances."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import sqlbot_xpack  # Match main.py initialization order for the bundled extension.
from langchain_core.messages import AIMessageChunk
from sqlalchemy import inspect
from sqlmodel import Session, create_engine

from apps.chat.api.chat import ask_recommend_questions
from apps.chat.models.chat_model import Chat, ChatRecord
from apps.chat.task.llm import LLMService


class RecommendQuestionSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Chat.__table__.create(self.engine)
        ChatRecord.__table__.create(self.engine)
        with Session(self.engine) as session:
            session.add(Chat(id=5, oid=124, create_by=1, datasource=7, engine_type='PostgreSQL'))
            session.add(ChatRecord(id=10, chat_id=5, create_by=1, datasource=7,
                                   question='统计订单数量', sql='SELECT COUNT(*) FROM orders',
                                   chart='{"type":"table"}'))
            session.commit()
        self.request_session = Session(self.engine)
        self.user = SimpleNamespace(id=1, oid=124)
        self.platform = patch('apps.chat.task.record_access.settings.PLATFORM_DATASOURCE_ENABLED', False)
        self.platform.start()

    def tearDown(self):
        self.platform.stop()
        self.request_session.close()
        self.engine.dispose()

    async def prepare_worker(self):
        service = LLMService.__new__(LLMService)
        service.ds = None
        service.current_logs = {}
        service.chat_question = SimpleNamespace(
            ai_modal_id=1, ai_modal_name='test',
            guess_sys_question=lambda count: 'Generate follow-up questions',
            guess_user_question=lambda questions: questions,
        )
        service.run_recommend_questions_task_async = Mock()
        service.await_result = lambda: iter(())
        with patch('apps.chat.api.chat.LLMService.create', AsyncMock(return_value=service)):
            await ask_recommend_questions(self.request_session, self.user, 10, None)
        service.run_recommend_questions_task_async.assert_called_once_with()
        return service

    def close_request(self):
        self.request_session.commit()
        self.request_session.close()

    def run_worker_and_verify_save(self, service):
        with Session(self.engine) as worker, \
                patch('apps.chat.task.llm.start_log'), patch('apps.chat.task.llm.end_log'):
            chunks = list(service.generate_recommend_questions_task(worker))
        self.assertEqual(chunks[-1]['recommended_question'], '["按月份统计订单数量"]')
        with Session(self.engine) as verify:
            record = verify.get(ChatRecord, 10)
            self.assertEqual(record.recommended_question, '["按月份统计订单数量"]')
            self.assertEqual(record.sql, 'SELECT COUNT(*) FROM orders')
            self.assertEqual(record.chart, '{"type":"table"}')

    async def test_request_closed_before_worker_starts(self):
        original = self.request_session.get(ChatRecord, 10)
        service = await self.prepare_worker()
        self.close_request()
        self.assertTrue(inspect(original).detached)
        self.assertTrue(inspect(original).expired)
        service._model_stream = lambda messages, *, operation: iter([
            AIMessageChunk(content='["按月份统计订单数量"]')
        ])
        self.run_worker_and_verify_save(service)

    async def test_request_closed_during_model_stream(self):
        service = await self.prepare_worker()

        def stream(messages, *, operation):
            self.close_request()
            yield AIMessageChunk(content='["按月份统计订单数量"]')

        service._model_stream = stream
        self.run_worker_and_verify_save(service)

    async def test_unauthorized_record_never_starts_worker(self):
        for user in (SimpleNamespace(id=2, oid=124), SimpleNamespace(id=1, oid=999)):
            with self.subTest(user=user), \
                    patch('apps.chat.api.chat.LLMService.create', AsyncMock()) as create, \
                    patch('apps.chat.api.chat.traceback.print_exc'):
                response = await ask_recommend_questions(self.request_session, user, 10, None)
                events = [event async for event in response.body_iterator]
                self.assertIn('无权访问此记录', ''.join(events))
                create.assert_not_awaited()
