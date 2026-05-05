"""
主线二 Phase 3a：中台表级权限校验客户端

调用中台 /openapi/sqlbot/table-permissions/check 接口校验当前用户对一组表是否有访问权限。

设计原则：
- fail-closed：网络错误 / 5xx / 解析失败 → PermissionCheckResult.allowed=False，并附 reason
- 仅在 PLATFORM_DATASOURCE_ENABLED=true 且配置了 BASE_URL 时启用；否则视为"不校验，放行"
- 透传当前请求的 Authorization 与 tenant-id header 给中台
- 同步实现（chat 主流程是同步生成器，避免再引入 await 链路改造）

返回结构与需求文档对齐：
{
  "allowed": bool,
  "deniedTables": [{"name", "displayName", "catalogPath", "reason", "applyUrl"}],
  "allowedTables": [{"name", "displayName", "catalogPath"}],
  "datasourceId": str
}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import requests

from common.core.config import settings
from common.utils.utils import SQLBotLogUtil


@dataclass
class DeniedTable:
    name: str
    display_name: Optional[str] = None
    catalog_path: Optional[str] = None
    reason: Optional[str] = None
    apply_url: Optional[str] = None


@dataclass
class PermissionCheckResult:
    allowed: bool
    denied_tables: list[DeniedTable] = field(default_factory=list)
    reason: Optional[str] = None  # 失败原因（网络 / 协议 / fail-closed）
    datasource_id: Optional[str] = None
    raw_payload: Optional[dict] = None  # 调用方需要原始 payload 时使用


def is_permission_check_enabled() -> bool:
    """
    Phase 3a 阶段：仅当数据源接入开关开启 + 配置 base url 时才启用权限校验。
    关闭时返回 False，调用方按"不校验放行"处理（保持改造前行为）。
    """
    return bool(settings.PLATFORM_DATASOURCE_ENABLED and settings.PLATFORM_DATASOURCE_BASE_URL)


def check_table_permissions(
    *,
    authorization_header: Optional[str],
    tenant_id: Optional[str | int],
    datasource_id: Optional[str | int],
    sql: str,
    tables: list[str],
    chat_id: Optional[int] = None,
    record_id: Optional[int] = None,
    timeout: Optional[float] = None,
) -> PermissionCheckResult:
    """
    调用中台权限接口。fail-closed：任何异常返回 allowed=False。

    参数:
        authorization_header: 透传给中台的 Authorization 头（含 "Bearer "）
        tenant_id: 透传给中台的 tenant-id 头
        datasource_id: 中台数据源 ID
        sql: 待执行的 SQL
        tables: 解析或合并后的表名列表
        chat_id / record_id: 审计上下文（可选）
        timeout: 覆盖默认 PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS
    """
    if not tables:
        # 没识别出任何表，视为放行（无可校验对象）；调用方应自行判断 SQL parse 是否失败
        return PermissionCheckResult(allowed=True, raw_payload={"tables": []})

    if not is_permission_check_enabled():
        # 配置未开启：视为不校验放行（保持改造前行为）
        return PermissionCheckResult(allowed=True, raw_payload={"skipped": "disabled"})

    base_url = settings.PLATFORM_DATASOURCE_BASE_URL.rstrip("/")
    url = f"{base_url}{settings.PLATFORM_PERMISSION_CHECK_PATH}"
    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }
    if authorization_header:
        headers["Authorization"] = authorization_header
    if tenant_id is not None:
        headers[settings.PLATFORM_AUTH_TENANT_ID_HEADER] = str(tenant_id)

    body = {
        "datasourceId": datasource_id,
        "sql": sql,
        "tables": [{"name": t} for t in tables],
    }
    if chat_id is not None:
        body["chatId"] = chat_id
    if record_id is not None:
        body["recordId"] = record_id

    effective_timeout = timeout if timeout is not None else settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=effective_timeout)
    except requests.RequestException as exc:
        SQLBotLogUtil.exception(f"Platform permission check HTTP error: {exc}")
        return PermissionCheckResult(
            allowed=False,
            reason=f"permission_service_unreachable: {exc}",
        )

    if resp.status_code != 200:
        SQLBotLogUtil.warning(f"Platform permission check non-200: {resp.status_code} {resp.text[:200]}")
        return PermissionCheckResult(
            allowed=False,
            reason=f"permission_service_status_{resp.status_code}",
        )

    try:
        body_json = resp.json()
    except Exception as exc:
        SQLBotLogUtil.warning(f"Platform permission check parse failed: {exc}")
        return PermissionCheckResult(
            allowed=False,
            reason="permission_service_invalid_json",
        )

    if not isinstance(body_json, dict) or body_json.get("code") not in (0, 200):
        SQLBotLogUtil.warning(f"Platform permission check biz failure: {body_json}")
        return PermissionCheckResult(
            allowed=False,
            reason=f"permission_service_biz_error: {body_json.get('message') if isinstance(body_json, dict) else 'unknown'}",
            raw_payload=body_json if isinstance(body_json, dict) else None,
        )

    data = body_json.get("data") or {}
    allowed = bool(data.get("allowed", False))
    denied_raw = data.get("deniedTables") or []
    denied = [
        DeniedTable(
            name=str(item.get("name", "")),
            display_name=item.get("displayName"),
            catalog_path=item.get("catalogPath"),
            reason=item.get("reason"),
            apply_url=item.get("applyUrl"),
        )
        for item in denied_raw
        if isinstance(item, dict)
    ]
    return PermissionCheckResult(
        allowed=allowed,
        denied_tables=denied,
        datasource_id=str(data.get("datasourceId")) if data.get("datasourceId") is not None else None,
        raw_payload=data,
    )


def denied_tables_to_sse_payload(result: PermissionCheckResult, gate_label: str) -> dict:
    """
    把 PermissionCheckResult 转成 SSE 友好结构（与 datasource_not_found 同款 schema）。
    gate_label: "gate1" 或 "gate2"，便于前端区分两次校验的提示位置。
    """
    return {
        "type": "permission_denied",
        "gate": gate_label,
        "reason": result.reason or "user_lacks_table_permission",
        "datasourceId": result.datasource_id,
        "deniedTables": [
            {
                "name": t.name,
                "displayName": t.display_name,
                "catalogPath": t.catalog_path,
                "reason": t.reason,
                "applyUrl": t.apply_url,
            }
            for t in result.denied_tables
        ],
        "actions": [
            {"type": "permission_apply", "label": "申请权限"},
        ],
    }
