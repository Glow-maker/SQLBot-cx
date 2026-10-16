"""Operator-managed knowledge. Platform snapshots are never query permissions."""
import logging
import threading
import time
from typing import Literal

import httpx
import sqlglot
from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import BigInteger, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from sqlmodel import Session

from apps.operations.knowledge_models import KnowledgeCatalog, KnowledgeEntry, utcnow
from apps.operations.security import identity, require_operator
from apps.chat.task.query_guard import bounded_stream, require_read_query
from apps.datasource.models.datasource import CoreDatasource
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentUser, SessionDep, Trans
from common.utils.platform_permission import check_table_permissions
from common.utils.sql_parser import extract_table_refs

router = APIRouter(prefix='/operations/knowledge', tags=['operations knowledge'])
Kind = Literal['terminology', 'data-training']
logger = logging.getLogger(__name__)
_query_embedding_slot = threading.BoundedSemaphore(1)
# Examples may teach later generated SQL, so keep their functions to ordinary analytics.
SAFE_SQL_FUNCTIONS = {
    'sum', 'avg', 'min', 'max', 'count', 'count_if', 'coalesce', 'nullif', 'if', 'case',
    'cast', 'try_cast', 'extract', 'date_trunc', 'timestamp_trunc', 'datetime_trunc',
    'str_to_time', 'time_to_str', 'to_char', 'to_date', 'date_add', 'date_sub', 'date_diff',
    'timestamp_diff', 'date', 'current_date', 'current_timestamp', 'year', 'month', 'day',
    'abs', 'round', 'ceil', 'ceiling', 'floor', 'length', 'lower', 'upper', 'trim', 'ltrim',
    'rtrim', 'substring', 'concat', 'concat_ws', 'replace', 'split_part', 'rank', 'dense_rank',
    'row_number', 'lag', 'lead', 'first_value', 'last_value', 'greatest', 'least', 'power',
    'sqrt', 'mod', 'date_part', 'date_format', 'lpad', 'rpad',
}


class EnableBody(BaseModel):
    enabled: bool


class KnowledgeBody(BaseModel):
    id: int | None = Field(default=None, gt=0)
    word: str | None = Field(default=None, max_length=255)
    question: str | None = Field(default=None, max_length=255)
    description: str = Field(max_length=4000)
    other_words: list[str] = Field(default_factory=list, max_length=20)
    specific_ds: bool = False
    datasource_ids: list[int] = Field(default_factory=list, max_length=100)
    datasource: int | None = Field(default=None, gt=0)
    advanced_application: int | None = Field(default=None, gt=0)
    enabled: bool = False
    report_id: int | None = Field(default=None, gt=0)
    draft_id: str | None = Field(default=None, max_length=100)


def clean_catalog(data):
    """Whitelist old Data catalog fields; never serialize a datasource object."""
    if not isinstance(data, list):
        raise ValueError('Invalid catalog')
    result = []
    for source in data:
        source_id = int(source['id'])
        if source_id <= 0:
            raise ValueError('Invalid datasource identity')
        tables = []
        for table in source.get('tables') or []:
            name = str(table.get('name') or '').strip()
            if not name:
                continue
            schema = str(table.get('schemaName') or source.get('schema') or source.get('db_schema') or '')
            if '.' in name:
                prefix, name = name.rsplit('.', 1)
                schema = schema or prefix
            table_id = table.get('tableId') or table.get('id')
            category_id = table.get('categoryId')
            tables.append({'tableId': int(table_id) if table_id is not None else None,
                'categoryId': int(category_id) if category_id is not None else None, 'schemaName': schema, 'name': name,
                'fields': [{'name': str(f['name']), 'type': str(f.get('type') or '')}
                           for f in table.get('fields') or [] if f.get('name')]})
        result.append({'id': source_id, 'name': str(source.get('name') or source_id),
                       'type': str(source.get('type') or ''), 'tables': tables})
    return result


def catalog_rows(session, tenant_id):
    return session.scalars(select(KnowledgeCatalog).where(KnowledgeCatalog.tenant_id == tenant_id)
                           .order_by(KnowledgeCatalog.datasource_id)).all()


def merge_catalog(session, tenant_id, sources):
    # A user's partial catalog cannot globally delete another operator's observed sources.
    for source in sources:
        row = session.scalar(select(KnowledgeCatalog).where(KnowledgeCatalog.tenant_id == tenant_id,
                                                          KnowledgeCatalog.datasource_id == source['id']))
        if row is None:
            row = KnowledgeCatalog(tenant_id=tenant_id, datasource_id=source['id'])
            session.add(row)
        old = {(str(t.get('tableId') or ''), t['schemaName'], t['name']): t
               for t in (row.metadata_json or {}).get('tables', [])}
        old.update({(str(t.get('tableId') or ''), t['schemaName'], t['name']): t for t in source['tables']})
        row.metadata_json = dict(source, tables=list(old.values()))
        row.updated_at = utcnow()
    session.commit()


@router.get('/catalog')
def catalog(session: SessionDep, user: CurrentUser, scope: str = 'platform', tenant_id: int | None = None):
    scope, tenant = require_operator(session, user, tenant_id, scope)
    if scope == 'local':
        sources = [{'id': ds.id, 'name': ds.name, 'type': ds.type, 'tables': [], 'updated_at': None}
                   for ds in session.scalars(select(CoreDatasource).where(CoreDatasource.oid == tenant)).all()]
    else:
        sources = [dict(row.metadata_json, updated_at=row.updated_at) for row in catalog_rows(session, tenant)]
    return {'scope': scope, 'tenant_id': tenant, 'sources': sources,
            'can_refresh': scope == 'platform' and identity(user)[0] == 'platform' and tenant == int(user.oid)}


@router.post('/catalog/refresh')
async def refresh_catalog(request: Request, session: SessionDep, user: CurrentUser,
                          scope: str = 'platform', tenant_id: int | None = None):
    scope, tenant = require_operator(session, user, tenant_id, scope)
    if scope != 'platform' or identity(user)[0] != 'platform' or tenant != int(user.oid):
        raise HTTPException(403, '请由目标租户的中台运营管理员刷新目录')
    if not settings.PLATFORM_DATASOURCE_ENABLED or not settings.PLATFORM_DATASOURCE_BASE_URL:
        raise HTTPException(503, '中台目录服务未配置')
    token = request.headers.get(settings.TOKEN_KEY) or request.headers.get('Authorization')
    try:
        async with httpx.AsyncClient(timeout=settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(settings.PLATFORM_DATASOURCE_BASE_URL.rstrip('/') + '/' +
                settings.PLATFORM_DATASOURCE_LIST_PATH.lstrip('/'),
                headers={'Authorization': token, settings.PLATFORM_AUTH_TENANT_ID_HEADER: str(tenant)})
        response.raise_for_status()
        body = response.json()
        if body.get('code') not in (0, 200):
            raise ValueError('Catalog rejected')
        sources = clean_catalog(body['data'])
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(502, '中台目录读取失败，原管理目录保持不变') from None
    merge_catalog(session, tenant, sources)
    return {'datasource_count': len(sources), 'table_count': sum(len(s['tables']) for s in sources)}


def entry_query(tenant_id, kind):
    return select(KnowledgeEntry).where(KnowledgeEntry.source_kind == 'platform',
                                       KnowledgeEntry.tenant_id == tenant_id, KnowledgeEntry.kind == kind)


def source_condition(session, kind, datasource_ids):
    if kind == 'data-training':
        return KnowledgeEntry.payload['datasource'].as_integer().in_(datasource_ids)
    ids = KnowledgeEntry.payload['datasource_ids']
    if session.get_bind().dialect.name == 'postgresql':
        values = func.jsonb_array_elements_text(cast(ids, JSONB)).table_valued('value')
    else:  # SQLite is used by the isolated regression fixture.
        values = func.json_each(ids).table_valued('value')
    overlap = select(1).select_from(values).where(cast(values.c.value, BigInteger).in_(datasource_ids))
    return or_(KnowledgeEntry.payload['specific_ds'].as_boolean().is_(False),
               overlap.correlate(KnowledgeEntry).exists())


def get_entry(session, tenant_id, kind, entry_id):
    row = session.scalar(entry_query(tenant_id, kind).where(KnowledgeEntry.id == entry_id))
    if row is None:
        raise HTTPException(404, '当前范围内不存在该知识条目')
    return row


def view_entry(row, sources):
    value = dict(row.payload, id=str(row.id), oid=str(row.tenant_id), enabled=row.enabled,
                 create_time=row.created_at, source_kind=row.source_kind)
    value['datasource_names'] = [sources.get(i, str(i)) for i in value.get('datasource_ids', [])]
    value['datasource_name'] = sources.get(value.get('datasource'), '')
    return value


@router.get('/{kind}/page/{page}/{size}')
def pager(kind: Kind, page: int, size: int, session: SessionDep, user: CurrentUser,
          scope: str = 'platform', tenant_id: int | None = None, word: str = '', question: str = '',
          dslist: list[int] | None = Query(None)):
    scope, tenant = require_operator(session, user, tenant_id, scope)
    if page < 1 or not 1 <= size <= 100:
        raise HTTPException(422, '分页范围无效')
    if scope == 'local':
        if kind == 'terminology':
            from apps.terminology.curd.terminology import page_terminology
            result = page_terminology(session, page, size, word, tenant, dslist)
        else:
            from apps.data_training.curd.data_training import page_data_training
            result = page_data_training(session, page, size, question, tenant)
        return dict(zip(('current_page', 'page_size', 'total_count', 'total_pages', 'data'), result))
    query = entry_query(tenant, kind)
    if isinstance(dslist, list) and dslist:
        if len(dslist) > 100:
            raise HTTPException(422, '数据源筛选最多100项')
        query = query.where(source_condition(session, kind, dslist))
    keyword = word if kind == 'terminology' else question
    if keyword.strip():
        query = query.where(KnowledgeEntry.label.ilike('%' + keyword.strip() + '%'))
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id.desc())
                           .offset((page - 1) * size).limit(size)).all()
    sources = {r.datasource_id: r.metadata_json['name'] for r in catalog_rows(session, tenant)}
    return {'current_page': page, 'page_size': size, 'total_count': count,
            'total_pages': (count + size - 1) // size, 'data': [view_entry(r, sources) for r in rows]}


def validate_sql(sql, tables, dialect=None):
    # Platform connection types are not SQLGlot dialect names; normalize only
    # the parser input. Unknown types still fail instead of using a fallback.
    if dialect:
        dialect = dialect.lower()
        dialect = {'pg': 'postgres', 'postgresql': 'postgres', 'opengauss': 'postgres',
                   'kingbase': 'postgres', 'excel': 'postgres', 'dm': 'oracle',
                   'sqlserver': 'tsql', 'ck': 'clickhouse', 'mariadb': 'mysql'}.get(dialect, dialect)
    try:
        require_read_query(sql, dialect)
        parsed = sqlglot.parse_one(sql, read=dialect)
        for function in parsed.find_all(exp.Func):
            name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
            if name.lower() not in SAFE_SQL_FUNCTIONS:
                raise ValueError('SQL示例只能使用常规统计、日期和文本函数')
        refs = extract_table_refs(sql, dialect)
        if not refs:
            raise ValueError('示例必须引用当前数据源中的业务表')
        resolved = []
        for ref in refs:
            matches = [t for t in tables if str(t['name']).lower() == ref.name.lower()
                       and (not ref.schema or str(t.get('schemaName') or '').lower() == ref.schema.lower())]
            if ref.catalog or len(matches) != 1:
                raise ValueError('示例包含范围外或不明确的表，请使用 schema.table')
            resolved.append(matches[0])
        # Validate known physical columns, including those inside CTEs/subqueries.
        for sql_scope in traverse_scope(parsed):
            physical = {}
            derived = False
            for alias, (_, source) in sql_scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    match = [t for t in resolved if str(t['name']).lower() == source.name.lower()
                             and (not source.db or str(t.get('schemaName') or '').lower() == source.db.lower())]
                    if len(match) == 1:
                        physical[alias.lower()] = {str(f['name']).lower() for f in match[0].get('fields', [])}
                else:
                    derived = True
            for column in sql_scope.columns:
                name = column.name.lower()
                if name == '*':
                    continue
                if column.table and column.table.lower() in physical:
                    if name not in physical[column.table.lower()]:
                        raise ValueError('示例引用了当前目录中不存在的字段')
                elif not column.table and physical and not derived:
                    output_aliases = {e.alias.lower() for e in sql_scope.expression.expressions if e.alias}
                    if name not in output_aliases and not any(name in fields for fields in physical.values()):
                        raise ValueError('示例引用了当前目录中不存在的字段')
        return resolved
    except (sqlglot.errors.SqlglotError, ValueError, KeyError) as exc:
        raise ValueError(str(exc)) from None


def validate_payload(session, tenant_id, kind, body):
    name_key = 'word' if kind == 'terminology' else 'question'
    label = str(body.get(name_key) or '').strip()
    description = str(body.get('description') or '').strip()
    if not label or len(label) > 255 or not description or len(description) > (1200 if kind == 'terminology' else 4000):
        raise HTTPException(422, '名称/问题必填且最多255字，术语说明最多1200字，SQL示例最多4000字')
    sources = {r.datasource_id: r.metadata_json for r in catalog_rows(session, tenant_id)}
    if kind == 'terminology':
        specific = bool(body.get('specific_ds'))
        ids = list(dict.fromkeys(int(i) for i in body.get('datasource_ids') or [])) if specific else []
        if specific and (not ids or any(i not in sources for i in ids)):
            raise HTTPException(422, '请选择当前租户管理目录中的数据源')
        synonyms = list(dict.fromkeys(str(v).strip() for v in body.get('other_words') or [] if str(v).strip()))
        if len(synonyms) > 20 or any(len(s) > 128 for s in synonyms):
            raise HTTPException(422, '同义词最多20个，每个最多128字')
        return {'word': label, 'description': description, 'other_words': synonyms,
                'specific_ds': specific, 'datasource_ids': ids}
    ds = int(body.get('datasource') or 0)
    if ds not in sources or body.get('advanced_application'):
        raise HTTPException(422, '请选择当前租户的平台数据源')
    try:
        validate_sql(description, sources[ds]['tables'], sources[ds]['type'])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {'question': label, 'description': description, 'datasource': ds}


def local_record(session, tenant, kind, item_id):
    from apps.data_training.models.data_training_model import DataTraining
    from apps.terminology.models.terminology_model import Terminology
    model = Terminology if kind == 'terminology' else DataTraining
    row = session.scalar(select(model).where(model.id == item_id, model.oid == tenant))
    if row is None or (kind == 'terminology' and row.pid is not None):
        raise HTTPException(404, '当前工作空间不存在该知识条目')
    return row


def save_local(session, tenant, kind, body, trans):
    if body.get('id'):
        local_record(session, tenant, kind, int(body['id']))
    else:
        body = dict(body, enabled=False)
    ids = body.get('datasource_ids', []) if kind == 'terminology' else [body.get('datasource')]
    ids = [int(i) for i in ids if i is not None]
    allowed = set(session.scalars(select(CoreDatasource.id).where(CoreDatasource.oid == tenant)).all())
    if not set(ids).issubset(allowed):
        raise HTTPException(403, '数据源不属于当前工作空间')
    if body.get('advanced_application'):
        from apps.system.models.system_model import AssistantModel
        application = session.scalar(select(AssistantModel).where(
            AssistantModel.id == int(body['advanced_application']), AssistantModel.oid == tenant,
            AssistantModel.type == 1))
        if application is None:
            raise HTTPException(403, '高级应用不属于当前工作空间')
    if kind == 'terminology':
        from apps.terminology.curd.terminology import create_terminology, update_terminology
        from apps.terminology.models.terminology_model import TerminologyInfo
        info = TerminologyInfo.model_validate(body)
        return (update_terminology if info.id else create_terminology)(session, info, tenant, trans,
                                                                      skip_embedding=True, commit=False)
    from apps.data_training.curd.data_training import create_training, update_training
    from apps.data_training.models.data_training_model import DataTrainingInfo
    info = DataTrainingInfo.model_validate(body)
    return (update_training if info.id else create_training)(session, info, tenant, trans,
                                                            skip_embedding=True, commit=False)


def embed_entry(entry_id):
    if not settings.EMBEDDING_ENABLED:
        return
    try:
        from apps.ai_model.embedding import EmbeddingModelCache
        with Session(engine) as session:
            row = session.get(KnowledgeEntry, entry_id)
            if row is None:
                return
            label, modified = row.label, row.updated_at
            text = label + ' ' + ' '.join(row.payload.get('other_words') or [])
            vector = EmbeddingModelCache.get_model().embed_query(text)
            session.refresh(row)
            if row.updated_at == modified:
                row.embedding = vector
                session.commit()
    except Exception as exc:
        logger.warning('Knowledge embedding unavailable id=%s error=%s', entry_id, type(exc).__name__)


@router.put('/{kind}')
def save(kind: Kind, body: dict, background: BackgroundTasks, session: SessionDep, user: CurrentUser,
         trans: Trans, scope: str = 'platform', tenant_id: int | None = None):
    scope, tenant = require_operator(session, user, tenant_id, scope)
    try:
        body = KnowledgeBody.model_validate(body).model_dump(exclude_unset=True)
    except ValidationError:
        raise HTTPException(422, '知识条目字段格式或长度无效') from None
    report = None
    if bool(body.get('report_id')) != bool(body.get('draft_id')):
        raise HTTPException(422, '报告和草稿标识必须同时提供')
    if body.get('report_id'):
        from apps.operations.reports import get_report_draft
        report, draft = get_report_draft(session, user, int(body['report_id']), str(body['draft_id']))
        draft_kind = 'data-training' if draft['kind'] == 'sql_example' else draft['kind']
        if report.scope != scope or report.tenant_id != tenant or draft_kind != kind or body.get('id'):
            raise HTTPException(403, '报告草稿与当前知识范围不一致')
    if scope == 'local':
        item_id = save_local(session, tenant, kind, body, trans)
    else:
        payload = validate_payload(session, tenant, kind, body)
        label = payload['word' if kind == 'terminology' else 'question']
        if body.get('id'):
            row = get_entry(session, tenant, kind, int(body['id']))
            row.enabled = bool(body.get('enabled', row.enabled))
        else:
            row = KnowledgeEntry(source_kind='platform', tenant_id=tenant, kind=kind, enabled=False)
            session.add(row)
        row.label, row.payload, row.embedding, row.updated_at = label, payload, None, utcnow()
        session.flush()
        item_id = row.id
    if report is not None:
        from apps.operations.reports import mark_draft_accepted
        mark_draft_accepted(session, report, str(body['draft_id']), item_id)
    session.commit()
    if scope == 'platform':
        background.add_task(embed_entry, item_id)
    else:
        from common.utils.embedding_threads import run_save_data_training_embeddings, run_save_terminology_embeddings
        background.add_task(run_save_terminology_embeddings if kind == 'terminology' else run_save_data_training_embeddings,
                            [item_id])
    return item_id


@router.delete('/{kind}')
def remove(kind: Kind, session: SessionDep, user: CurrentUser, ids: list[int] = Body(...),
           scope: str = 'platform', tenant_id: int | None = None):
    scope, tenant = require_operator(session, user, tenant_id, scope)
    if not ids or len(ids) > 100:
        raise HTTPException(422, '请选择1至100条记录')
    if scope == 'local':
        for item_id in ids:
            local_record(session, tenant, kind, item_id)
        if kind == 'terminology':
            from apps.terminology.curd.terminology import delete_terminology
            delete_terminology(session, ids, tenant)
        else:
            from apps.data_training.curd.data_training import delete_training
            delete_training(session, ids, tenant)
    else:
        rows = [get_entry(session, tenant, kind, i) for i in ids]
        for row in rows:
            session.delete(row)
        session.commit()
    return True


@router.put('/{kind}/{item_id}/enabled')
def enable(kind: Kind, item_id: int, body: EnableBody, session: SessionDep, user: CurrentUser,
           trans: Trans, scope: str = 'platform', tenant_id: int | None = None):
    scope, tenant = require_operator(session, user, tenant_id, scope)
    if scope == 'local':
        local_record(session, tenant, kind, item_id)
        if kind == 'terminology':
            from apps.terminology.curd.terminology import enable_terminology
            enable_terminology(session, item_id, body.enabled, trans, tenant)
        else:
            from apps.data_training.curd.data_training import enable_training
            enable_training(session, item_id, body.enabled, trans, tenant)
    else:
        row = get_entry(session, tenant, kind, item_id)
        if body.enabled:
            validate_payload(session, tenant, kind, row.payload)
        row.enabled, row.updated_at = body.enabled, utcnow()
        session.commit()
    return True


def recall_entries(session, tenant_id, kind, question, datasource_ids, vector=None):
    query = entry_query(tenant_id, kind).where(KnowledgeEntry.enabled.is_(True),
                                             source_condition(session, kind, datasource_ids))
    # A bounded lexical fallback remains usable without an embedding model.
    rows = session.scalars(query.order_by(KnowledgeEntry.updated_at.desc()).limit(200)).all()
    question_lower = question.lower()
    matching = [r for r in rows if any(w.lower() in question_lower or question_lower in w.lower()
                for w in [r.label, *(r.payload.get('other_words') or [])] if w)]
    if vector is not None:
        try:
            with session.begin_nested():
                threshold = settings.EMBEDDING_TERMINOLOGY_SIMILARITY if kind == 'terminology' else settings.EMBEDDING_DATA_TRAINING_SIMILARITY
                distance = KnowledgeEntry.embedding.cosine_distance(vector)
                matching += session.scalars(query.where(KnowledgeEntry.embedding.is_not(None), distance < 1 - threshold)
                    .order_by(distance).limit(10)).all()
        except Exception as exc:
            logger.warning('Knowledge recall lexical fallback kind=%s error=%s', kind, type(exc).__name__)
    result, seen = [], set()
    for row in matching:
        if row.id in seen:
            continue
        seen.add(row.id)
        payload = row.payload
        if kind == 'data-training' and payload.get('datasource') not in datasource_ids:
            continue
        if kind == 'terminology' and payload.get('specific_ds') and not set(payload.get('datasource_ids', [])).intersection(datasource_ids):
            continue
        result.append(row)
    return result


def query_vector(service):
    # Optional recall must not make the request wait on a cold/stalled local model.
    service._check_deadline()
    if not _query_embedding_slot.acquire(blocking=False):
        return None
    def encode():
        try:
            from apps.ai_model.embedding import EmbeddingModelCache
            yield EmbeddingModelCache.get_model().embed_query(service.chat_question.question)
        finally:
            _query_embedding_slot.release()
    try:
        deadline = min(getattr(service, 'query_deadline', time.monotonic() + 3), time.monotonic() + 3)
        result = list(bounded_stream(encode, deadline))
        return result[0] if result else None
    except Exception as exc:
        logger.warning('Knowledge query embedding unavailable error=%s', type(exc).__name__)
        return None
    finally:
        service._check_deadline()


def platform_templates(service, session):
    """Inject only current-route knowledge; directory snapshots never authorize a query."""
    if hasattr(service, '_operations_knowledge_templates'):
        return service._operations_knowledge_templates
    empty = ('', [], '', [])
    if identity(service.current_user)[0] != 'platform':
        return empty
    selected = getattr(service, 'logical_route_tables', None) or []
    if not selected:
        return empty
    service._operations_knowledge_templates = empty
    service.operations_knowledge = []
    tenant = int(service.current_user.oid)
    ds_ids = {int(t['datasourceId']) for t in selected}
    try:
        # No model or permission request when this tenant has no published knowledge.
        if session.scalar(select(KnowledgeEntry.id).where(KnowledgeEntry.source_kind == 'platform',
            KnowledgeEntry.tenant_id == tenant, KnowledgeEntry.enabled.is_(True)).limit(1)) is None:
            return empty
        vector = None
        if settings.EMBEDDING_ENABLED:
            vector = query_vector(service)
        terms = recall_entries(session, tenant, 'terminology', service.chat_question.question, ds_ids, vector)
        examples = recall_entries(session, tenant, 'data-training', service.chat_question.question, ds_ids, vector)
        if not terms and not examples:
            return empty
        permitted = set()
        # At most one request per selected source, with the current user's token.
        for ds_id in ds_ids:
            service._check_deadline()
            entries = [t for t in selected if int(t['datasourceId']) == ds_id]
            result = check_table_permissions(authorization_header=service._extract_authorization_header(),
                tenant_id=tenant, datasource_id=ds_id, sql='',
                tables=[{'name': t['name'], 'schemaName': t.get('schemaName'),
                         'tableId': t.get('tableId'), 'categoryId': t.get('categoryId')} for t in entries],
                chat_id=getattr(service.record, 'chat_id', None), record_id=service.record.id,
                timeout=max(.1, min(settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS,
                                    getattr(service, 'query_deadline', time.monotonic() + 10) - time.monotonic())))
            if result.allowed:
                permitted.add(ds_id)
        if not permitted:
            return empty
        term_list, example_list, hits = [], [], []
        term_chars = example_chars = 0
        for row in terms:
            p = row.payload
            if p.get('specific_ds') and not set(p['datasource_ids']).intersection(permitted):
                continue
            if term_chars + len(p['description']) > 2500:
                continue
            term_list.append({'words': [p['word'], *p['other_words']], 'description': p['description']})
            hits.append({'kind': 'terminology', 'id': row.id, 'source_kind': 'platform'})
            term_chars += len(p['description'])
            if len(term_list) == 5:
                break
        for row in examples:
            p = row.payload
            ds_id = p['datasource']
            if ds_id not in permitted or example_chars + len(p['description']) > 6000:
                continue
            entries = [t for t in selected if int(t['datasourceId']) == ds_id]
            try:
                validate_sql(p['description'], entries, entries[0].get('datasourceType'))
            except ValueError:
                continue
            example_list.append({'question': p['question'], 'suggestion-answer': p['description']})
            hits.append({'kind': 'data-training', 'id': row.id, 'source_kind': 'platform'})
            example_chars += len(p['description'])
            if len(example_list) == 3:
                break
        from apps.terminology.curd.terminology import to_xml_string as terms_xml
        from apps.data_training.curd.data_training import to_xml_string as examples_xml
        from apps.template.generate_chart.generator import get_base_terminology_template, get_base_data_training_template
        result = (get_base_terminology_template().format(terminologies=terms_xml(term_list)) if term_list else '', term_list,
                  get_base_data_training_template().format(data_training=examples_xml(example_list)) if example_list else '', example_list)
        service._operations_knowledge_templates = result
        service.operations_knowledge = hits
        logger.info('Knowledge recall record_id=%s terms=%s examples=%s', service.record.id, len(term_list), len(example_list))
        return result
    except Exception as exc:
        logger.warning('Knowledge injection skipped record_id=%s error=%s', service.record.id, type(exc).__name__)
        return empty
