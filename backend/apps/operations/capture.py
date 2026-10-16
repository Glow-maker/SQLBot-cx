"""Bounded operational copies; no result rows, prompts, credentials or ORM objects cross threads."""
import json
import logging
import re
import time
from datetime import timedelta
from threading import Event, Thread

from sqlalchemy import delete, update, func
from sqlmodel import Session, select

from apps.operations.models import Feedback, Interaction, ReportTask, now_utc
from apps.operations.security import identity

log = logging.getLogger(__name__)


def safe_summary(value):
    text = str(value or '').split('Traceback (')[0]
    text = re.sub(r'(?i)(?:https?|postgres(?:ql)?|mysql|redis)://[^\s\"\']+', '[地址已隐藏]', text)
    text = re.sub(r'(?i)(password|passwd|api[_-]?key|authorization|token|secret)\s*[=:]\s*[^\s,;]+', r'\1=[已隐藏]', text)
    text = re.sub(r'(?i)bearer\s+[^\s,;]+', 'Bearer [已隐藏]', text)
    # DB exceptions often include row values / SQL below the first line.
    return text.splitlines()[0][:1500] if text else ''


def classify_error(value, error_class=None):
    try:
        payload = json.loads(str(value))
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, TypeError):
        payload = {}
    kind, reason = payload.get('type'), payload.get('reason')
    message = safe_summary(payload.get('message') or value)
    if kind == 'query_timeout' or error_class in {'TimeoutError', 'APITimeoutError', 'ReadTimeout'}:
        return 'timeout', 'model_or_query_timeout', message
    if kind == 'permission_denied':
        code = payload.get('errorCode', 'table_permission_denied')
        return ('permission_denied' if code == 'table_permission_denied' else 'error'), code, message
    if kind == 'datasource_not_found':
        return ('index_not_ready' if reason == 'index_not_ready' else 'no_match'), reason or kind, message
    if kind == 'cross_source_not_supported':
        return 'clarification', kind, message
    return 'error', kind or error_class or 'unknown_error', message


class Capture:
    def __init__(self, interaction_id, bind, started):
        self.id, self.bind, self.started = interaction_id, bind, started
        self.record_id = None
        self.stages, self.models, self.knowledge = {}, [], []
        self.error = self.error_class = None
        self.guidance = False
        self.predict_failed = False
        self.first_output_ms = None
        self.query_outcome = None

    def attach(self, service):
        service.operations_capture = self

    def observe(self, chunk):
        if isinstance(chunk, str) and chunk.startswith('data:'):
            try:
                payload = json.loads(chunk[5:])
            except (ValueError, TypeError):
                return
        elif isinstance(chunk, dict):
            payload = chunk
        else:
            return
        kind = payload.get('type')
        if kind == 'progress':
            # Store only bounded stage metadata, never arbitrary SSE content.
            stage = payload.get('stage')
            if stage in {'route', 'connect', 'generate', 'permission', 'query', 'result'}:
                self.stages[(stage, payload.get('detailStage', ''))] = {
                    key: payload[key] for key in ('stage', 'status', 'elapsedMs', 'detailStage', 'detailStatus', 'detailElapsedMs')
                    if key in payload}
        elif kind == 'error' or payload.get('success') is False:
            self.error = payload.get('content') or payload.get('message') or '任务未完成'
        elif kind == 'guidance':
            self.guidance = True
        elif kind == 'predict-failed':
            self.predict_failed = True

    def model(self, operation, elapsed_ms, first_output_ms, usage, status, error):
        if self.first_output_ms is None and first_output_ms is not None:
            self.first_output_ms = round((time.perf_counter() - self.started) * 1000 - elapsed_ms + first_output_ms, 1)
        self.models.append(dict(stage=operation, status=status, elapsedMs=round(elapsed_ms, 1),
                                first_output_ms=first_output_ms, total_tokens=usage.get('total_tokens'), error_class=error))
        if error:
            self.error_class = error

    def failure(self, exc):
        self.error, self.error_class = str(exc), type(exc).__name__

    def chart_failure(self, exc, elapsed_ms):
        """Keep a presentation failure visible without replacing a completed query."""
        error_class = type(exc).__name__
        status = 'timeout' if error_class in {'TimeoutError', 'APITimeoutError', 'ReadTimeout'} else 'error'
        for model in reversed(self.models):
            if model['stage'] == 'GENERATE_CHART':
                model.update(status=status, error_class=error_class)
                return
        self.models.append(dict(stage='GENERATE_CHART', status=status,
                                elapsedMs=round(elapsed_ms, 1), error_class=error_class))

    def query_result(self, rows):
        # Keep only the shape classification, never result cells or a row copy.
        self.query_outcome = None
        if not isinstance(rows, list):
            return
        if not rows:
            self.query_outcome = 'empty_result'
        elif all(isinstance(row, dict) and row for row in rows):
            self.query_outcome = ('all_null_result' if all(
                value is None for row in rows for value in row.values()) else 'rows_returned')

    def finish(self, service=None):
        try:
            self._finish(service)
        except Exception as exc:
            # Observability failure must not replace a completed answer.
            log.error('Operations snapshot failed id=%s class=%s', self.id, type(exc).__name__)

    def _finish(self, service):
        with Session(self.bind) as session:
            item = session.get(Interaction, self.id)
            if not item or item.status != 'running':
                return
            if service is not None:
                record = getattr(service, 'record', None)
                record_id = getattr(record, 'id', None)
                if record_id:
                    from apps.chat.models.chat_model import ChatRecord
                    saved = session.get(ChatRecord, record_id)
                    item.record_id = record_id
                    if saved:
                        item.sql = (saved.sql or '')[:30000] or None
                        item.datasource_id = saved.datasource
                        # Recommendation shares the main record but has its own outcome.
                        if item.kind != 'recommend' and saved.error and not self.error:
                            self.error = saved.error
                item.knowledge_json = getattr(service, 'operations_knowledge', [])[:8]
            if self.predict_failed:
                item.status, item.outcome = 'no_match', 'prediction_not_available'
            elif self.error:
                item.status, item.outcome, item.error_summary = classify_error(self.error, self.error_class)
                item.error_class = self.error_class
            elif self.guidance:
                item.status, item.outcome = 'clarification', 'greeting'
            else:
                item.status = 'success'
                item.outcome = (self.query_outcome if item.kind in {'question', 'retry'} else None) or 'completed'
            item.finished_at = now_utc()
            item.duration_ms = round((time.perf_counter() - self.started) * 1000, 1)
            item.stages_json = list(self.stages.values())[:30] + self.models[:30]
            item.first_output_ms = self.first_output_ms
            usages = [m.get('total_tokens') for m in self.models]
            item.total_tokens = sum(usages) if usages and all(isinstance(v, int) for v in usages) else None
            values = item.model_dump(exclude={'id'})
            session.expunge(item)
            session.exec(update(Interaction).where(Interaction.id == self.id, Interaction.status == 'running').values(**values))
            session.commit()

    def link_record(self, service):
        record_id = getattr(getattr(service, 'record', None), 'id', None)
        if not record_id:
            return
        try:
            with Session(self.bind) as session:
                session.exec(update(Interaction).where(Interaction.id == self.id, Interaction.status == 'running')
                             .values(record_id=record_id))
                session.commit()
        except Exception as exc:
            log.error('Operations record link failed id=%s class=%s', self.id, type(exc).__name__)


def begin(session, user, question, kind='question', parent_record_id=None):
    from apps.chat.models.chat_model import Chat
    from fastapi import HTTPException
    chat = session.get(Chat, question.chat_id)
    if not chat or chat.create_by != user.id or chat.oid != user.oid:
        raise HTTPException(403, '无权访问此会话')
    # Legacy integration test doubles / internal callers without a verified
    # identity are not attributed to a guessed namespace.
    if getattr(user, 'auth_source', 'unknown') == 'unknown':
        return None
    source, tenant, uid = identity(user)
    started = time.perf_counter()
    try:
        with Session(session.get_bind()) as own:
            where = [Interaction.auth_source == source, Interaction.tenant_id == tenant,
                     Interaction.user_id == uid, Interaction.chat_id == question.chat_id,
                     Interaction.expires_at > now_utc()]
            parent = own.exec(select(Interaction).where(*where, Interaction.record_id == parent_record_id,
                Interaction.kind.in_(['question', 'retry'])).order_by(Interaction.id.desc())).first() if parent_record_id else None
            rounds = own.exec(select(func.count()).select_from(Interaction).where(*where, Interaction.kind == 'question')).one()
            root_id = (parent.root_question_id or parent.id) if parent else None
            retries = own.exec(select(func.count()).select_from(Interaction).where(*where,
                Interaction.root_question_id == root_id, Interaction.kind == 'retry')).one() if root_id else 0
            item = Interaction(scope='platform' if source == 'platform' else 'local', auth_source=source,
                tenant_id=tenant, user_id=uid, account=user.account[:100], user_name=user.name[:100],
                chat_id=question.chat_id, root_question_id=root_id, kind=kind, question=(question.question or '')[:10000],
                round_count=(parent.round_count if parent else rounds + (1 if kind == 'question' else 0)),
                retry_count=retries + (1 if kind == 'retry' else 0))
            own.add(item)
            own.commit()
            own.refresh(item)
            if not item.root_question_id and kind in {'question', 'retry'}:
                item.root_question_id = item.id
                own.add(item)
                own.commit()
                own.refresh(item)
            return Capture(item.id, session.get_bind(), started)
    except Exception as exc:
        log.error('Operations start failed class=%s', type(exc).__name__)
        return None


def run_captured(service, generator):
    capture = getattr(service, 'operations_capture', None)
    if capture:
        capture.link_record(service)
    try:
        for chunk in generator:
            if capture:
                capture.observe(chunk)
                if isinstance(chunk, dict):
                    chunk['interaction_id'] = capture.id
            service.chunk_list.append(chunk)
    except Exception as exc:
        if capture:
            capture.failure(exc)
        raise
    finally:
        if capture:
            capture.finish(service)


def cleanup(bind, now=None):
    now = now or now_utc()
    with Session(bind) as session:
        # A model task has a 120s budget; leave a conservative 10min lease for
        # initialization. Do not classify HTTP stream disconnection as cancellation.
        session.exec(update(Interaction).where(Interaction.status == 'running',
            Interaction.created_at < now - timedelta(minutes=10)).values(
                status='interrupted', outcome='worker_interrupted', finished_at=now,
                error_summary='任务未记录完成，可能因服务重启或工作进程中断'))
        for model in (Feedback, ReportTask, Interaction):
            session.exec(delete(model).where(model.expires_at <= now))
        session.commit()


def start_cleanup(bind):
    stop = Event()
    def work():
        while not stop.is_set():
            try:
                cleanup(bind)
            except Exception as exc:
                log.error('Operations retention failed class=%s', type(exc).__name__)
            stop.wait(3600)
    thread = Thread(target=work, daemon=True, name='sqlbot-operations-retention')
    thread.start()
    return stop
