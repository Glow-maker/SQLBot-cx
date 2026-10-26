"""Revalidate stored platform results against the current user's catalog."""
import json
from fastapi import HTTPException
from apps.chat.models.chat_model import Chat, ChatRecord, ChatLog, OperationEnum
from apps.system.schemas.permission import RequestContext
from common.core.config import settings


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


def require_record_access(session, user, record_id, *, current_assistant=None, check_candidates=True):
    """check_candidates=False when only stored results are shown: the record's SQL is still rechecked,
    but routing candidates matter only when the stored prompt is sent to the model again."""
    record = session.get(ChatRecord, record_id)
    chat = session.get(Chat, record.chat_id) if record else None
    if not record or not chat or record.create_by != user.id or chat.create_by != user.id or chat.oid != user.oid:
        raise HTTPException(403, "无权访问此记录")
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
        service.out_ds_instance = None
        service.ds = None
        service.record = record
        service.logical_route_tables = []
        service.logical_route_physical_datasource_key = None
        if not service.current_assistant:
            raise HTTPException(403, "当前授权上下文不可用")
        catalog = service._ensure_out_ds_instance()
        for log in route_logs:
            require_current_candidates(log.messages, service._logical_table_entries())
        if not record.sql:
            return record
        service.ds = next((ds for ds in catalog.ds_list or [] if ds.id == record.datasource), None)
        if service.ds is None:
            raise HTTPException(403, "当前无权访问此记录的数据源")
        from apps.system.crud.assistant import AssistantOutDs
        service.logical_route_physical_datasource_key = AssistantOutDs._physical_datasource_key(service.ds)
        check = service._do_permission_check(record.sql, None, "stored_result")
        if not check or not check.allowed:
            raise HTTPException(403, "数据授权已变化，请使用当前可查询表重新提问")
    return record
