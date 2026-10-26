"""Revalidate stored platform results against the current user's catalog."""
import json
from fastapi import HTTPException
from apps.chat.models.chat_model import Chat, ChatRecord, ChatLog, OperationEnum
from apps.system.schemas.permission import RequestContext
from common.core.config import settings
from common.utils.utils import extract_nested_json


def require_current_candidates(messages, entries):
    """Stored prompts can contain candidates beyond the final SQL's tables."""
    current = {(str(e['datasourceId']), str(e['tableId'])): e for e in entries}
    for message in messages or []:
        if message.get('type') != 'human':
            continue
        try:
            cards = json.loads(message['content'])['tables']
            for card in cards:
                entry = current.get((str(card['datasourceId']), str(card['tableId'])))
                if not entry or any(card.get(k) != entry.get(k) for k in ('schemaName', 'name')):
                    raise ValueError('candidate revoked')
                fields = {f['name'] for f in entry.get('fields') or []}
                # Empty structure means Data could not read an unregistered table this time, not a removed field.
                if fields and any(f['name'] not in fields for f in card.get('fields') or []):
                    raise ValueError('field removed')
        except (KeyError, TypeError, ValueError):
            raise HTTPException(403, '历史目录授权已变化，请使用当前可查询表重新提问') from None


def routed_entries(record, entries):
    """Current catalog entries the record was routed to; tables no longer in the catalog stay out."""
    try:
        content = json.loads(record.datasource_select_answer or '{}').get('content') or ''
        tables = json.loads(extract_nested_json(content) or '{}').get('tables') or []
    except (TypeError, ValueError, AttributeError):
        return []
    # Same keys the live router accepts (llm.py route_logical_datasource).
    routed = {(str(t.get('datasourceId') or t.get('datasource_id')), str(t.get('tableId') or t.get('id')))
              for t in tables if isinstance(t, dict)}
    return [e for e in entries if (str(e.get('datasourceId')), str(e.get('tableId'))) in routed]


def require_record_owner(session, user, record_id):
    record = session.get(ChatRecord, record_id)
    chat = session.get(Chat, record.chat_id) if record else None
    if not record or not chat or record.create_by != user.id or chat.create_by != user.id or chat.oid != user.oid:
        raise HTTPException(403, "无权访问此记录")
    return record


def require_record_access(session, user, record_id, *, current_assistant=None, check_candidates=True, catalog=None):
    """check_candidates=False when only stored results are shown: the record's SQL is still rechecked,
    but routing candidates matter only when the stored prompt is sent to the model again.
    catalog: the AssistantOutDs already loaded in this request, so a chat with many records reads it once."""
    record = require_record_owner(session, user, record_id)
    base_id = getattr(record, 'analysis_record_id', None) or getattr(record, 'predict_record_id', None)
    if settings.PLATFORM_DATASOURCE_ENABLED and base_id and not record.sql:
        # Analysis/predict records copy the base record's chart and rows without its SQL.
        require_record_access(session, user, base_id, current_assistant=current_assistant,
                              check_candidates=False, catalog=catalog)
    route_logs = []
    if settings.PLATFORM_DATASOURCE_ENABLED and check_candidates:
        route_logs = session.query(ChatLog).filter(ChatLog.pid == record_id,
            ChatLog.operate == OperationEnum.CHOOSE_DATASOURCE).all()
    if settings.PLATFORM_DATASOURCE_ENABLED and (record.sql or route_logs):
        from apps.chat.task.llm import LLMService
        service = LLMService.__new__(LLMService)
        service.current_user = user
        if current_assistant is None:
            request = RequestContext.get_request()
            current_assistant = getattr(request.state, 'assistant', None)
        service.current_assistant = current_assistant
        service.out_ds_instance = catalog
        service.ds = None
        service.record = record
        service.logical_route_tables = []
        service.logical_route_physical_datasource_key = None
        if not service.current_assistant:
            raise HTTPException(403, "当前授权上下文不可用")
        try:
            catalog = service._ensure_out_ds_instance()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "问数目录暂不可用，请稍后重试") from None
        for log in route_logs:
            require_current_candidates(log.messages, service._logical_table_entries())
        if not record.sql:
            return record
        service.ds = next((ds for ds in catalog.ds_list or [] if ds.id == record.datasource), None)
        if service.ds is None:
            raise HTTPException(403, "当前无权访问此记录的数据源")
        from apps.system.crud.assistant import AssistantOutDs
        service.logical_route_physical_datasource_key = AssistantOutDs._physical_datasource_key(service.ds)
        # Same table disambiguation as the live gates had; Data still checks every resolved table.
        service.logical_route_tables = routed_entries(record, service._logical_table_entries())
        check = service._do_permission_check(record.sql, None, "stored_result")
        reason = (getattr(check, 'reason', None) or '') if check else ''
        if reason == 'permission_service_status_401':
            raise HTTPException(401, "登录状态已失效，请重新登录后重试")
        if reason.startswith('permission_service_'):
            raise HTTPException(503, "权限服务暂不可用，请稍后重试")
        if not check or not check.allowed:
            raise HTTPException(403, "数据授权已变化，请使用当前可查询表重新提问")
    return record
