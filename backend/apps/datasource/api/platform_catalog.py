"""Question starters from the current user's authorized platform catalog only."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Response

from apps.datasource.api.platform_embeddings import platform_identity
from common.core.config import settings

router = APIRouter(prefix="/datasource/platform", tags=["Datasource"])


@router.get("/question-starters")
async def question_starters(response: Response, auth=Depends(platform_identity)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, tenant-id"
    identity, token = auth
    base = settings.PLATFORM_DATASOURCE_BASE_URL
    if not settings.PLATFORM_DATASOURCE_ENABLED or not base:
        raise HTTPException(503, "中台数据源服务未配置")
    try:
        async with httpx.AsyncClient(timeout=settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS) as client:
            upstream = await client.get(
                base.rstrip("/") + "/" + settings.PLATFORM_DATASOURCE_LIST_PATH.lstrip("/"),
                headers={"Authorization": f"Bearer {token}",
                         settings.PLATFORM_AUTH_TENANT_ID_HEADER: str(identity.tenant_id)},
            )
        if upstream.status_code in (401, 403):
            raise HTTPException(upstream.status_code, "中台登录或目录权限校验失败")
        upstream.raise_for_status()
        body = upstream.json()
        if not isinstance(body, dict):
            raise ValueError("Invalid platform catalog")
        if body.get("code") in (401, 403):
            raise HTTPException(int(body["code"]), "中台登录或目录权限校验失败")
        if body.get("code") != 0 or not isinstance(body.get("data"), list):
            raise ValueError("Invalid platform catalog")
        tables = []
        seen = set()
        for source in body["data"]:
            for table in source.get("tables") or []:
                # Re-read the authorized directory for every request. Indexes and local
                # datasource lists must never expand this user's visible table set.
                physical_name = str(table.get("name") or "").strip()
                if not physical_name:
                    continue
                key = (source.get("id"), table.get("schemaName"),
                       table.get("tableId") or table.get("id"), physical_name)
                if key in seen:
                    continue
                seen.add(key)
                if len(tables) >= 6:
                    continue
                name = str(table.get("categoryName") or table.get("comment") or physical_name).strip()
                description = str(table.get("categoryComment") or table.get("comment") or "").strip()
                source_name = str(source.get("name") or "").strip()
                # Include the source name to avoid silently choosing between same-name tables.
                subject = f"{source_name}中的{name}" if source_name else name
                tables.append({"name": name, "description": description,
                               "question": f"查看{subject}的前10条数据"})
        return {"tables": tables, "total": len(seen)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(502, "暂时无法加载可查询目录，请稍后重试") from None
