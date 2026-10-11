"""Manual platform indexing API. Authorization always remains owned by the platform."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, Field, ValidationError

from common.core.config import settings
from common.core.token_resolver import resolve_platform_token_identity
from apps.datasource.embedding.platform_index import get_platform_index
from apps.datasource.embedding.platform_text import MetadataSnapshot

router = APIRouter(prefix="/platform/table-embeddings", tags=["platform_embeddings"])


class SyncRequest(BaseModel):
    datasourceId: int = Field(gt=0)
    force: bool = False


async def platform_identity(request: Request):
    scheme, token = get_authorization_scheme_param(request.headers.get(settings.TOKEN_KEY) or request.headers.get("Authorization"))
    if scheme.lower() != "bearer" or not token or not settings.PLATFORM_AUTH_ENABLED:
        raise HTTPException(401, "需要有效的中台登录身份")
    try:
        identity = await resolve_platform_token_identity(token)
    except Exception:
        raise HTTPException(503, "中台身份服务暂不可用") from None
    if identity is None:
        raise HTTPException(401, "中台登录已失效")
    tenant = request.headers.get(settings.PLATFORM_AUTH_TENANT_ID_HEADER)
    if tenant != str(identity.tenant_id):
        raise HTTPException(403, "租户不匹配")
    return identity, token


async def platform_get(datasource_id, auth, operation):
    identity, token = auth
    base = settings.PLATFORM_DATASOURCE_BASE_URL
    if not settings.PLATFORM_DATASOURCE_ENABLED or not base:
        raise HTTPException(503, "中台数据源服务未配置")
    try:
        async with httpx.AsyncClient(timeout=settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base.rstrip('/')}/openapi/sqlbot/datasources/{datasource_id}/{operation}",
                headers={"Authorization": f"Bearer {token}", settings.PLATFORM_AUTH_TENANT_ID_HEADER: str(identity.tenant_id)})
        if response.status_code in (401, 403):
            raise HTTPException(response.status_code, "中台登录或同步权限校验失败")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Invalid platform response")
        if body.get("code") in (401, 403):
            raise HTTPException(int(body["code"]), "中台登录或同步权限校验失败")
        if body.get("code") != 0:
            raise HTTPException(502, "中台未能完成元数据或权限查询")
        return body["data"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(502, "中台元数据服务暂不可用") from None


async def check_access(datasource_id, auth):
    result = await platform_get(datasource_id, auth, "embedding-access")
    if not isinstance(result, dict) or result.get("tenantId") != auth[0].tenant_id or result.get("datasourceId") != datasource_id:
        raise HTTPException(403, "中台同步权限校验失败")


@router.post("/sync", status_code=202)
async def sync(body: SyncRequest, auth=Depends(platform_identity)):
    data = await platform_get(body.datasourceId, auth, "embedding-metadata")
    try:
        snapshot = MetadataSnapshot.model_validate(data)
    except ValidationError:
        raise HTTPException(502, "中台元数据清单不完整，未启动索引同步") from None
    if snapshot.tenantId != auth[0].tenant_id or snapshot.datasourceId != body.datasourceId:
        raise HTTPException(403, "元数据范围不匹配")
    return await run_in_threadpool(get_platform_index().submit, auth[0].tenant_id, auth[0].user_id, snapshot.model_dump(), body.force)


@router.get("/sync")
async def latest(datasourceId: int = Query(gt=0), auth=Depends(platform_identity)):
    await check_access(datasourceId, auth)
    return await run_in_threadpool(get_platform_index().get, auth[0].tenant_id, None, datasourceId)


@router.get("/sync/{jobId}")
async def status(jobId: str, auth=Depends(platform_identity)):
    job = await run_in_threadpool(get_platform_index().get, auth[0].tenant_id, jobId)
    if job is None:
        raise HTTPException(404, "同步任务不存在")
    await check_access(job["datasourceId"], auth)
    return job
