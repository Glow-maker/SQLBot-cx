import asyncio
import json
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import sqlbot_xpack
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

from apps.chat.models.chat_model import Chat, ChatRecord, ChatQuestion
from apps.operations.api import (FeedbackInput, FeedbackReply, Filters, access, feedback_list,
    interaction_detail, my_feedback, overview, reply_feedback, submit_feedback, tenants, users)
from apps.operations.capture import Capture, begin, classify_error, cleanup, run_captured, safe_summary
from apps.operations.models import Feedback, Interaction, OperationsGrant, ReportTask, now_utc
from apps.operations.security import is_instance_admin, require_operator
from apps.operations.knowledge_models import KnowledgeCatalog
from apps.system.models.system_model import WorkspaceModel


def user(source='platform', tenant=124, uid=1, admin=False):
    return SimpleNamespace(auth_source=source, oid=tenant, id=uid, account='admin' if admin else 'tester',
                           name='测试用户', isAdmin=admin)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        for model in (Interaction, Feedback, ReportTask, OperationsGrant, Chat, ChatRecord):
            model.__table__.create(self.engine)
        KnowledgeCatalog.__table__.create(self.engine)
        WorkspaceModel.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.session.add(Chat(id=10, oid=124, create_by=1, engine_type='PostgreSQL'))
        self.session.add(ChatRecord(id=20, chat_id=10, create_by=1, question='统计订单',
                                    sql='SELECT COUNT(*) FROM orders', error=None))
        self.session.commit()
        self.platform = user()
        self.admin = user('local', tenant=1, admin=True)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def capture(self, **kwargs):
        result = begin(self.session, self.platform, ChatQuestion(chat_id=10, question='统计订单'), **kwargs)
        self.assertIsNotNone(result)
        return result

    def grant(self):
        self.session.add(OperationsGrant(tenant_id=124, user_id=1, account='tester', updated_by=1))
        self.session.commit()

    def saved(self, capture):
        self.session.expire_all()
        return self.session.get(Interaction, capture.id)

    def test_platform_admin_id_is_not_instance_admin_and_grants_are_scoped(self):
        elevated = user(admin=True)
        self.assertFalse(is_instance_admin(elevated))
        with self.assertRaises(HTTPException):
            require_operator(self.session, elevated)
        self.grant()
        self.assertEqual(require_operator(self.session, elevated), ('platform', 124))
        for who, scope, tenant in [(elevated, 'platform', 125), (elevated, 'local', 124),
                                  (user('local'), 'platform', 124), (user('assistant', admin=True), 'local', 124)]:
            with self.subTest(who=who, scope=scope), self.assertRaises(HTTPException):
                require_operator(self.session, who, tenant, scope)
        self.assertEqual(require_operator(self.session, self.admin, 125, 'platform'), ('platform', 125))

    def test_ownership_checked_before_record_start(self):
        for who in (user(tenant=125), user(uid=2)):
            with self.assertRaises(HTTPException):
                begin(self.session, who, ChatQuestion(chat_id=10, question='x'))
        self.assertEqual(self.session.exec(select(Interaction)).all(), [])

    def test_failed_answer_can_submit_feedback_without_data_permission_and_idempotently(self):
        capture = self.capture()
        service = SimpleNamespace(record=SimpleNamespace(id=20))
        capture.link_record(service)
        capture.failure(ValueError(json.dumps({'type': 'permission_denied', 'errorCode': 'table_permission_denied',
                                               'message': '无查询权限'})))
        capture.finish(service)
        body = FeedbackInput(record_id=20, type='permission', content='只能看见目录', submission_id=uuid4())
        first = submit_feedback(body, self.session, self.platform)
        second = submit_feedback(body, self.session, self.platform)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(self.saved(capture).status, 'permission_denied')
        for who in (user('local'), user(tenant=125), user(uid=2)):
            with self.assertRaises(HTTPException):
                submit_feedback(body, self.session, who)

    def test_feedback_reply_visible_to_owner_not_other_users(self):
        body = FeedbackInput(type='other', content='建议增加解释', submission_id=uuid4())
        row = submit_feedback(body, self.session, self.platform)
        with self.assertRaises(HTTPException):
            reply_feedback(row['id'], FeedbackReply(status='resolved', reply='已处理'), self.session, self.platform)
        self.grant()
        reply_feedback(row['id'], FeedbackReply(status='resolved', reply='已处理'), self.session, self.platform)
        self.assertEqual(my_feedback(self.session, self.platform, page=1, size=20)['items'][0]['reply'], '已处理')
        self.assertEqual(my_feedback(self.session, user(uid=2), page=1, size=20)['total'], 0)
        with self.assertRaises(HTTPException):
            feedback_list(self.session, user(tenant=125), Filters(tenant_id=124, scope='platform'))

    def test_recommend_failure_does_not_rewrite_success_and_counts_separate_retry(self):
        first = self.capture()
        first.finish(SimpleNamespace(record=SimpleNamespace(id=20)))
        retry = self.capture(kind='retry', parent_record_id=20)
        retry.failure(TimeoutError('模型超时'))
        retry.finish()
        recommendation = self.capture(kind='recommend', parent_record_id=20)
        recommendation.failure(RuntimeError('DetachedInstanceError'))
        recommendation.finish(SimpleNamespace(record=SimpleNamespace(id=20)))
        self.assertEqual(self.saved(first).status, 'success')
        self.assertEqual(self.saved(retry).root_question_id, first.id)
        self.assertEqual(self.saved(retry).retry_count, 1)
        stats = overview(self.session, self.admin, Filters(tenant_id=124, scope='platform'))
        self.assertEqual((stats['questions'], stats['retries'], stats['timeout'], stats['errors']), (1, 1, 1, 1))
        records = users(self.session, self.admin, Filters(tenant_id=124, scope='platform'))
        self.assertEqual(records['items'][0]['total'], 3)

    def test_snapshots_survive_chat_delete_and_cleanup_never_deletes_chat(self):
        capture = self.capture()
        capture.finish(SimpleNamespace(record=SimpleNamespace(id=20)))
        self.session.delete(self.session.get(Chat, 10))
        self.session.commit()
        result = interaction_detail(capture.id, self.session, self.admin)
        self.assertEqual(result['question'], '统计订单')
        item = self.session.get(Interaction, capture.id)
        item.expires_at = now_utc() - timedelta(seconds=1)
        self.session.add(item)
        self.session.add(Feedback(scope='platform', auth_source='platform', tenant_id=124, user_id=1,
            submission_id=str(uuid4()), type='other', content='expired', expires_at=item.expires_at))
        self.session.commit()
        cleanup(self.engine)
        self.session.expire_all()
        self.assertIsNone(self.session.get(Interaction, capture.id))
        self.assertEqual(self.session.exec(select(Feedback)).all(), [])
        self.assertIsNotNone(self.session.get(ChatRecord, 20))

    def test_unknown_token_usage_is_not_zero_and_snapshots_exclude_result_data(self):
        capture = self.capture()
        capture.model('GENERATE_SQL', 50, 10, {}, 'completed', None)
        capture.observe('data:' + json.dumps({'type': 'progress', 'stage': 'query', 'status': 'completed',
                                             'elapsedMs': 12, 'content': 'secret result'}))
        capture.finish(SimpleNamespace(record=SimpleNamespace(id=20), operations_knowledge=[{'id': 1, 'kind': 'terminology'}]))
        item = self.saved(capture)
        self.assertIsNone(item.total_tokens)
        self.assertEqual(item.knowledge_json[0]['id'], 1)
        self.assertNotIn('secret result', json.dumps(item.model_dump(), default=str))

    def test_interrupted_task_terminal_state_not_overwritten(self):
        capture = self.capture()
        item = self.saved(capture)
        item.created_at = now_utc() - timedelta(minutes=11)
        self.session.add(item)
        self.session.commit()
        cleanup(self.engine)
        capture.finish()
        self.assertEqual(self.saved(capture).status, 'interrupted')

    def test_guidance_and_denials_are_not_success(self):
        capture = self.capture()
        capture.observe({'type': 'guidance', 'content': '你好'})
        capture.finish()
        self.assertEqual(self.saved(capture).status, 'clarification')
        cases = [({'type': 'datasource_not_found', 'reason': 'index_not_ready'}, 'index_not_ready'),
                 ({'type': 'datasource_not_found', 'reason': 'no_relevant_table'}, 'no_match'),
                 ({'type': 'permission_denied', 'errorCode': 'permission_service_error'}, 'error')]
        for payload, expected in cases:
            self.assertEqual(classify_error(json.dumps(payload))[0], expected)

    def test_redaction_omits_connection_strings_and_credentials(self):
        message = safe_summary('password=abc token=xyz https://user:pass@host/path\nSQL: SELECT private_value')
        for secret in ('abc', 'xyz', 'user:pass', 'private_value'):
            self.assertNotIn(secret, message)

    def test_initialization_failure_is_recorded_and_stream_has_interaction_id(self):
        from apps.chat.api.chat import stream_sql
        async def exercise():
            with patch('apps.chat.api.chat.LLMService.create', AsyncMock(side_effect=ValueError('model unavailable'))), \
                    patch('apps.chat.api.chat.traceback.print_exc'):
                response = await stream_sql(self.session, self.platform, ChatQuestion(chat_id=10, question='统计订单'))
                return ''.join([chunk async for chunk in response.body_iterator])
        events = asyncio.run(exercise())
        self.assertIn('interaction_id', events)
        self.session.expire_all()
        row = self.session.exec(select(Interaction)).one()
        self.assertEqual(row.status, 'error')
        self.assertIsNone(row.record_id)
        self.assertEqual(row.error_class, 'ValueError')

    def test_worker_records_error_and_is_not_tied_to_consuming_http_stream(self):
        capture = self.capture()
        service = SimpleNamespace(operations_capture=capture, record=SimpleNamespace(id=20), chunk_list=[])
        def generate():
            yield 'data:{"type":"sql-data","content":"execute-success"}\n\n'
            raise TimeoutError('timeout')
        with self.assertRaises(TimeoutError):
            run_captured(service, generate())
        self.assertEqual(self.saved(capture).status, 'timeout')
        self.assertEqual(len(service.chunk_list), 1)

    def test_generic_feedback_tenant_discovery_and_expired_idempotency(self):
        body = FeedbackInput(type='other', content='首次通用反馈', submission_id=uuid4())
        result = submit_feedback(body, self.session, user(tenant=125))
        self.assertIn(('platform', 125), [(r['scope'], r['tenant_id']) for r in tenants(self.session, self.admin)])
        row = self.session.get(Feedback, result['id'])
        row.expires_at = now_utc() - timedelta(seconds=1)
        self.session.add(row)
        self.session.commit()
        with self.assertRaises(HTTPException) as caught:
            submit_feedback(body, self.session, user(tenant=125))
        self.assertEqual(caught.exception.status_code, 410)

    def test_scope_filter_distinguishes_ask_and_local_user_id(self):
        for source in ('local', 'ask'):
            self.session.add(Interaction(scope='local', auth_source=source, tenant_id=124, user_id=1, question=source))
        self.session.commit()
        result = overview(self.session, self.admin, Filters(tenant_id=124, scope='local', user_id=1, auth_source='ask'))
        self.assertEqual(result['total'], 1)

    def test_non_chat_progress_and_prediction_no_data_remain_factual(self):
        from apps.chat.task.progress import ChatProgress
        capture = self.capture(kind='predict', parent_record_id=20)
        progress = ChatProgress(False, observer=capture.observe)
        self.assertEqual(progress.update('query', 'completed'), [])
        capture.predict_failed = True
        capture.observe({'success': False, 'message': 'not enough data'})
        capture.finish()
        row = self.saved(capture)
        self.assertEqual(row.status, 'no_match')
        self.assertEqual(row.outcome, 'prediction_not_available')
        self.assertEqual(row.stages_json[0]['stage'], 'query')
