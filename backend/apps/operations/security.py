"""Full backend access requires verified local or platform administrator authority."""
from fastapi import HTTPException
from sqlmodel import select

from apps.operations.models import OperationsGrant


def identity(user):
    source = getattr(user, 'auth_source', 'unknown')
    if source not in {'local', 'platform', 'ask', 'assistant'}:
        raise HTTPException(403, '无法确认登录身份来源，请重新登录')
    return source, int(user.oid), int(user.id)


def is_instance_admin(user):
    source = getattr(user, 'auth_source', None)
    return bool(getattr(user, 'isAdmin', False)) and (
        source == 'local' or (source == 'platform' and getattr(user, 'platform_admin_verified', False)))


def require_operator(session, user, tenant_id=None, scope=None):
    source, own_tenant, user_id = identity(user)
    target_scope = scope or ('platform' if source == 'platform' else 'local')
    target_tenant = int(tenant_id if tenant_id is not None else own_tenant)
    if target_scope not in {'local', 'platform'} or target_tenant < 1:
        raise HTTPException(422, '请选择有效的来源和租户')
    if is_instance_admin(user):
        return target_scope, target_tenant
    if source == 'platform' and target_scope == 'platform' and target_tenant == own_tenant:
        grant = session.exec(select(OperationsGrant).where(
            OperationsGrant.tenant_id == own_tenant, OperationsGrant.user_id == user_id,
            OperationsGrant.enabled == True)).first()
        if grant:
            return target_scope, target_tenant
    raise HTTPException(403, '当前账号没有该租户的问数运营权限')


def require_instance_admin(user):
    if not is_instance_admin(user):
        raise HTTPException(403, '仅管理员可以维护运营授权')
