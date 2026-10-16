"""Legacy knowledge endpoints address local workspaces, never platform tenants."""
from fastapi import HTTPException

from common.core.deps import CurrentUser


def require_local_knowledge_user(user: CurrentUser):
    if getattr(user, 'auth_source', 'unknown') != 'local':
        raise HTTPException(403, '平台知识管理请使用运营设置中的术语库或SQL示例入口')

