from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from apps.operations.models import Feedback, Interaction, OperationsGrant, now_utc
from apps.operations.security import identity, is_instance_admin, require_instance_admin, require_operator
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(prefix='/operations', tags=['SQLBot operations'])


class Filters(BaseModel):
    tenant_id: int | None = None
    scope: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    user_id: int | None = None
    auth_source: str | None = None
    keyword: str | None = Field(default=None, max_length=200)
    status: str | None = None
    outcome: Literal['empty_result', 'all_null_result', 'rows_returned'] | None = None
    datasource_id: int | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


def conditions(session, user, filters, model=Interaction):
    scope, tenant = require_operator(session, user, filters.tenant_id, filters.scope)
    result = [model.scope == scope, model.tenant_id == tenant, model.expires_at > now_utc()]
    if filters.date_from:
        start = filters.date_from
        result.append(model.created_at >= (start.astimezone(timezone.utc).replace(tzinfo=None) if start.tzinfo else start))
    if filters.date_to:
        end = filters.date_to
        result.append(model.created_at < (end.astimezone(timezone.utc).replace(tzinfo=None) if end.tzinfo else end))
    if filters.user_id is not None:
        result.append(model.user_id == filters.user_id)
    if filters.auth_source:
        result.append(model.auth_source == filters.auth_source)
    if filters.keyword:
        column = model.question if model == Interaction else model.content
        result.append(column.contains(filters.keyword, autoescape=True))
    if filters.status:
        result.append(model.status == filters.status)
    if filters.datasource_id is not None and model == Interaction:
        result.append(model.datasource_id == filters.datasource_id)
    if filters.outcome and model == Interaction:
        result.append(model.outcome == filters.outcome)
    return result


def page_result(session, model, where, page, size):
    total = session.exec(select(func.count()).select_from(model).where(*where)).one()
    items = session.exec(select(model).where(*where).order_by(model.created_at.desc(), model.id.desc())
                         .offset((page - 1) * size).limit(size)).all()
    return dict(items=[row.model_dump() for row in items], total=total, page=page, size=size)


@router.get('/access')
def access(session: SessionDep, current_user: CurrentUser):
    source, tenant, _ = identity(current_user)
    operator = False
    try:
        require_operator(session, current_user)
        operator = True
    except HTTPException:
        pass
    return dict(instance_admin=is_instance_admin(current_user), operator=operator,
                tenant_id=tenant, auth_source=source)


@router.get('/tenants')
def tenants(session: SessionDep, current_user: CurrentUser):
    if not is_instance_admin(current_user):
        scope, tenant = require_operator(session, current_user)
        return [dict(scope=scope, tenant_id=tenant, name=f'租户 {tenant}')]
    from apps.operations.knowledge_models import KnowledgeCatalog
    from apps.system.models.system_model import WorkspaceModel
    pairs = {(row.scope, row.tenant_id) for row in session.exec(
        select(Interaction.scope, Interaction.tenant_id).where(Interaction.expires_at > now_utc()).distinct()).all()}
    pairs.update(('platform', row.tenant_id) for row in session.exec(select(OperationsGrant)).all())
    pairs.update((row.scope, row.tenant_id) for row in session.exec(select(Feedback.scope, Feedback.tenant_id)
        .where(Feedback.expires_at > now_utc()).distinct()).all())
    pairs.update(('local', row.id) for row in session.exec(select(WorkspaceModel)).all())
    # The snapshot is metadata only; it is never a grant to query data.
    pairs.update(('platform', row.tenant_id) for row in session.exec(select(KnowledgeCatalog)).all())
    pairs.add(('platform' if current_user.auth_source == 'platform' else 'local', current_user.oid))
    return [dict(scope=s, tenant_id=t, name=f'{"本地空间" if s == "local" else "中台租户"} {t}')
            for s, t in sorted(pairs)]


@router.get('/overview')
def overview(session: SessionDep, current_user: CurrentUser, filters: Filters = Depends()):
    where = conditions(session, current_user, filters)
    counts = dict(session.exec(select(Interaction.status, func.count()).where(*where).group_by(Interaction.status)).all())
    kinds = dict(session.exec(select(Interaction.kind, func.count()).where(*where).group_by(Interaction.kind)).all())
    outcomes = dict(session.exec(select(Interaction.outcome, func.count()).where(*where,
        Interaction.status == 'success', Interaction.outcome.in_(['empty_result', 'all_null_result', 'rows_returned']))
        .group_by(Interaction.outcome)).all())
    avg = session.exec(select(func.avg(Interaction.duration_ms)).where(*where)).one()
    users_count = session.exec(select(func.count()).select_from(select(Interaction.auth_source, Interaction.user_id)
        .where(*where).distinct().subquery())).one()
    feedback_where = conditions(session, current_user, filters.model_copy(update={'status': None}), Feedback)
    pending = session.exec(select(func.count()).select_from(Feedback).where(*feedback_where, Feedback.status == 'pending')).one()
    return dict(total=sum(counts.values()), questions=kinds.get('question', 0), retries=kinds.get('retry', 0),
                users=users_count, success=counts.get('success', 0), errors=counts.get('error', 0),
                timeout=counts.get('timeout', 0), permission_denied=counts.get('permission_denied', 0),
                avg_duration_ms=round(avg, 1) if avg is not None else None, feedback_pending=pending,
                status_counts=counts, kind_counts=kinds, outcome_counts=outcomes,
                empty_results=outcomes.get('empty_result', 0), all_null_results=outcomes.get('all_null_result', 0))


@router.get('/users')
def users(session: SessionDep, current_user: CurrentUser, filters: Filters = Depends()):
    where = conditions(session, current_user, filters)
    stmt = select(Interaction.user_id, Interaction.auth_source, func.max(Interaction.account).label('account'),
        func.max(Interaction.user_name).label('user_name'), func.count().label('total'),
        func.sum(case((Interaction.kind == 'question', 1), else_=0)).label('questions'),
        func.sum(case((Interaction.kind == 'retry', 1), else_=0)).label('retries'),
        func.sum(case((Interaction.status.in_(['error', 'timeout', 'interrupted']), 1), else_=0)).label('errors'),
        func.avg(Interaction.duration_ms).label('avg_duration_ms'), func.max(Interaction.created_at).label('last_seen'))
    stmt = stmt.where(*where).group_by(Interaction.user_id, Interaction.auth_source)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.order_by(func.max(Interaction.created_at).desc()).offset((filters.page - 1) * filters.size)
                        .limit(filters.size)).all()
    return dict(items=[dict(row._mapping) for row in rows], total=total, page=filters.page, size=filters.size)


@router.get('/interactions')
def interactions(session: SessionDep, current_user: CurrentUser, filters: Filters = Depends()):
    return page_result(session, Interaction, conditions(session, current_user, filters), filters.page, filters.size)


@router.get('/interactions/{interaction_id}')
def interaction_detail(interaction_id: int, session: SessionDep, current_user: CurrentUser):
    row = session.get(Interaction, interaction_id)
    if not row or row.expires_at <= now_utc():
        raise HTTPException(404, '记录不存在或已到保留期限')
    require_operator(session, current_user, row.tenant_id, row.scope)
    result = row.model_dump()
    result['feedback'] = [item.model_dump() for item in session.exec(select(Feedback).where(
        Feedback.interaction_id == row.id, Feedback.scope == row.scope, Feedback.tenant_id == row.tenant_id,
        Feedback.expires_at > now_utc())).all()]
    return result


class FeedbackInput(BaseModel):
    interaction_id: int | None = None
    record_id: int | None = None
    type: Literal['incorrect', 'slow', 'error', 'permission', 'suggestion', 'other']
    content: str = Field(min_length=1, max_length=4000)
    expected: str = Field(default='', max_length=2000)
    submission_id: UUID


def own_interaction(session, user, interaction_id=None, record_id=None):
    source, tenant, uid = identity(user)
    where = [Interaction.auth_source == source, Interaction.tenant_id == tenant, Interaction.user_id == uid,
             Interaction.expires_at > now_utc()]
    if interaction_id:
        where.append(Interaction.id == interaction_id)
    elif record_id:
        where.extend([Interaction.record_id == record_id, Interaction.kind.in_(['question', 'retry', 'analysis', 'predict'])])
    else:
        return None
    row = session.exec(select(Interaction).where(*where).order_by(Interaction.id.desc())).first()
    if not row:
        raise HTTPException(403, '无法确认该回答的归属，请使用通用反馈并说明问题')
    if record_id and row.record_id != record_id:
        raise HTTPException(403, '反馈关联记录不匹配')
    return row


@router.post('/feedback')
def submit_feedback(body: FeedbackInput, session: SessionDep, current_user: CurrentUser):
    source, tenant, uid = identity(current_user)
    # Only ownership is required: failed and unauthorized answers can be reported.
    interaction = own_interaction(session, current_user, body.interaction_id, body.record_id)
    key = [Feedback.auth_source == source, Feedback.tenant_id == tenant, Feedback.user_id == uid,
           Feedback.submission_id == str(body.submission_id)]
    existing = session.exec(select(Feedback).where(*key)).first()
    if existing:
        if existing.expires_at <= now_utc():
            raise HTTPException(410, '该次反馈已到保留期限，请发起新反馈')
        return existing.model_dump()
    item = Feedback(scope='platform' if source == 'platform' else 'local', auth_source=source, tenant_id=tenant, user_id=uid,
        account=current_user.account[:100], user_name=current_user.name[:100], submission_id=str(body.submission_id),
        interaction_id=interaction.id if interaction else None, record_id=interaction.record_id if interaction else None,
        type=body.type, content=body.content.strip(), expected=body.expected.strip())
    if not item.content:
        raise HTTPException(422, '请填写反馈内容')
    if interaction:
        item.expires_at = min(item.expires_at, interaction.expires_at)
    session.add(item)
    try:
        session.commit()
        session.refresh(item)
    except IntegrityError:
        session.rollback()
        item = session.exec(select(Feedback).where(*key)).first()
        if not item:
            raise
        if item.expires_at <= now_utc():
            raise HTTPException(410, '该次反馈已到保留期限，请发起新反馈')
    return item.model_dump()


@router.get('/feedback/mine')
def my_feedback(session: SessionDep, current_user: CurrentUser, record_id: int | None = None,
                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    source, tenant, uid = identity(current_user)
    where = [Feedback.auth_source == source, Feedback.tenant_id == tenant, Feedback.user_id == uid,
             Feedback.expires_at > now_utc()]
    if record_id:
        where.append(Feedback.record_id == record_id)
    return page_result(session, Feedback, where, page, size)


@router.get('/feedback')
def feedback_list(session: SessionDep, current_user: CurrentUser, filters: Filters = Depends()):
    return page_result(session, Feedback, conditions(session, current_user, filters, Feedback), filters.page, filters.size)


class FeedbackReply(BaseModel):
    status: Literal['pending', 'processing', 'resolved', 'declined']
    reply: str = Field(default='', max_length=2000)


@router.patch('/feedback/{feedback_id}')
def reply_feedback(feedback_id: int, body: FeedbackReply, session: SessionDep, current_user: CurrentUser):
    row = session.get(Feedback, feedback_id)
    if not row or row.expires_at <= now_utc():
        raise HTTPException(404, '反馈不存在或已到保留期限')
    require_operator(session, current_user, row.tenant_id, row.scope)
    row.status, row.reply = body.status, body.reply.strip()
    row.replied_by, row.replied_at = current_user.id, now_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


class GrantInput(BaseModel):
    tenant_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    account: str = Field(default='', max_length=100)
    enabled: bool = True


@router.get('/grants')
def grants(session: SessionDep, current_user: CurrentUser):
    require_instance_admin(current_user)
    return session.exec(select(OperationsGrant).order_by(OperationsGrant.tenant_id, OperationsGrant.user_id)).all()


@router.put('/grants')
def grant(body: GrantInput, session: SessionDep, current_user: CurrentUser):
    require_instance_admin(current_user)
    row = session.exec(select(OperationsGrant).where(OperationsGrant.tenant_id == body.tenant_id,
                      OperationsGrant.user_id == body.user_id)).first()
    if row is None:
        row = OperationsGrant(**body.model_dump(), updated_by=current_user.id)
    else:
        row.account, row.enabled = body.account, body.enabled
        row.updated_by, row.updated_at = current_user.id, now_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()
