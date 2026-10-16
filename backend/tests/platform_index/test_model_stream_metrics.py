import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from httpx import Request
from langchain_core.messages import AIMessageChunk
from openai import APITimeoutError

from apps.chat.models.chat_model import OperationEnum
from apps.chat.task.llm import LLMService, process_stream


class ModelStreamMetricsTests(unittest.TestCase):
    def service(self):
        service = LLMService.__new__(LLMService)
        service.record = SimpleNamespace(id=18)
        service.llm = MagicMock()
        service.query_deadline = time.monotonic() + 120
        return service

    def test_summary_measures_first_output_and_total_without_logging_content(self):
        service = self.service()
        clock = [10.0]
        chunks = [AIMessageChunk(content=''), AIMessageChunk(content='private SQL'),
                  AIMessageChunk(content=' result'), AIMessageChunk(content='', usage_metadata={
                      'input_tokens': 5032, 'output_tokens': 99, 'total_tokens': 5131})]

        def stream():
            for elapsed, chunk in zip((.01, .08, .12, .2), chunks):
                clock[0] = 10 + elapsed
                yield chunk
            clock[0] = 10.25

        service.llm.model_copy.return_value.stream.return_value = stream()
        with patch('apps.chat.task.llm.bounded_stream', side_effect=lambda factory, deadline: factory()), \
             patch('apps.chat.task.llm.time.perf_counter', side_effect=lambda: clock[0]), \
             patch('apps.chat.task.llm.SQLBotLogUtil.info') as log:
            actual = list(service._model_stream(['private prompt'], operation=OperationEnum.GENERATE_SQL))
        self.assertEqual(actual, chunks)
        self.assertTrue(all(a is b for a, b in zip(actual, chunks)))
        self.assertEqual(log.call_count, 2)
        summary = log.call_args.args[0]
        for value in ('record_id=18', 'operation=GENERATE_SQL', 'status=completed',
                      'first_output_ms=80.0', 'elapsed_ms=250.0', 'chunks=4',
                      'input_tokens=5032', 'output_tokens=99', 'total_tokens=5131'):
            self.assertIn(value, summary)
        self.assertNotIn('private', str(log.call_args_list))

    def test_reasoning_is_first_output_and_missing_usage_is_unknown(self):
        service = self.service()
        service.llm.model_copy.return_value.stream.return_value = iter([
            AIMessageChunk(content='', additional_kwargs={'reasoning_content': 'private reasoning'})])
        with patch('apps.chat.task.llm.bounded_stream', side_effect=lambda factory, deadline: factory()), \
             patch('apps.chat.task.llm.SQLBotLogUtil.info') as log:
            list(service._model_stream([], operation=OperationEnum.CHOOSE_DATASOURCE))
        summary = log.call_args.args[0]
        self.assertNotIn('first_output_ms=None', summary)
        self.assertIn('input_tokens=None', summary)
        self.assertNotIn('private reasoning', str(log.call_args_list))

    def test_failure_and_timeout_are_logged_and_propagated(self):
        for error, status in ((ValueError('private error'), 'error'), (TimeoutError('deadline'), 'timeout'),
                              (APITimeoutError(request=Request('POST', 'http://localhost/v1/chat/completions')), 'timeout')):
            with self.subTest(status=status):
                service = self.service()
                service.llm.model_copy.return_value.stream.side_effect = error
                with patch('apps.chat.task.llm.bounded_stream', side_effect=lambda factory, deadline: factory()), \
                     patch('apps.chat.task.llm.SQLBotLogUtil.info') as log:
                    with self.assertRaises(type(error)) as caught:
                        list(service._model_stream([], operation=OperationEnum.GENERATE_CHART))
                self.assertIs(caught.exception, error)
                summary = log.call_args.args[0]
                self.assertIn('status=' + status, summary)
                self.assertIn('error=' + type(error).__name__, summary)
                self.assertIn('first_output_ms=None', summary)
                self.assertNotIn('private error', str(log.call_args_list))

    def test_closing_consumer_logs_cancelled_and_closes_upstream(self):
        service = self.service()
        closed = []

        def stream():
            try:
                yield AIMessageChunk(content='first')
                yield AIMessageChunk(content='second')
            finally:
                closed.append(True)

        service.llm.model_copy.return_value.stream.return_value = stream()
        with patch('apps.chat.task.llm.bounded_stream', side_effect=lambda factory, deadline: factory()), \
             patch('apps.chat.task.llm.SQLBotLogUtil.info') as log:
            result = service._model_stream([], operation=OperationEnum.GENERATE_SQL)
            next(result)
            result.close()
        self.assertEqual(closed, [True])
        self.assertIn('status=cancelled', log.call_args.args[0])

    def test_existing_request_deadline_and_socket_timeout_are_preserved(self):
        service = self.service()
        service.query_deadline = 120
        with patch('apps.chat.task.llm.time.monotonic', return_value=100), \
             patch('apps.chat.task.llm.bounded_stream', return_value=iter([])) as bounded, \
             patch('apps.chat.task.llm.SQLBotLogUtil.info'):
            list(service._model_stream([], 30, operation=OperationEnum.CHOOSE_DATASOURCE))
        self.assertEqual(bounded.call_args.args[1], 120)
        service.llm.model_copy.assert_called_once_with(update={'request_timeout': 30, 'max_retries': 0})

    def test_process_stream_preserves_output_and_usage_without_chunk_logs(self):
        chunks = [AIMessageChunk(content='SELECT ', additional_kwargs={'reasoning_content': 'reason'}),
                  AIMessageChunk(content='1'), AIMessageChunk(content='', usage_metadata={
                      'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5})]
        usage = {}
        with patch('apps.chat.task.llm.SQLBotLogUtil.info') as log:
            actual = list(process_stream(iter(chunks), usage))
        self.assertEqual(''.join(chunk['content'] for chunk in actual), 'SELECT 1')
        self.assertEqual(''.join(chunk['reasoning_content'] for chunk in actual), 'reason')
        self.assertEqual(usage, {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5})
        log.assert_not_called()
