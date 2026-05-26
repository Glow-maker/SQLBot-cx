from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from common.core.config import settings

_platform_auth_redis_client: Redis | None = None


@dataclass
class PlatformTokenIdentity:
    user_id: int
    account: str
    tenant_id: int
    nickname: str
    payload: dict[str, Any]


def _normalize_redis_key(access_token: str) -> str:
    key_pattern = settings.PLATFORM_AUTH_REDIS_KEY_PREFIX
    if "%s" in key_pattern:
        return key_pattern % access_token
    return f"{key_pattern}{access_token}"


async def _get_platform_auth_redis_client() -> Redis | None:
    global _platform_auth_redis_client

    if not settings.PLATFORM_AUTH_ENABLED:
        return None
    redis_url = settings.PLATFORM_AUTH_REDIS_URL or settings.CACHE_REDIS_URL
    if not redis_url:
        return None
    if _platform_auth_redis_client is None:
        _platform_auth_redis_client = Redis.from_url(redis_url, decode_responses=True)
    return _platform_auth_redis_client


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # Java 常见的毫秒/秒级时间戳
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts)
        except (OSError, ValueError, OverflowError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # 纯数字字符串按时间戳处理
    if text.isdigit():
        return _parse_datetime(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        return expires_at <= datetime.now()
    return expires_at <= datetime.now(expires_at.tzinfo)


def _parse_json_object(raw_value: Any) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        return raw_value
    if not isinstance(raw_value, str):
        return None
    text = raw_value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    # 兼容二次 JSON 编码："{\"userId\":1,...}"
    if isinstance(parsed, str):
        try:
            reparsed = json.loads(parsed)
        except Exception:
            return None
        return reparsed if isinstance(reparsed, dict) else None
    return None


def _parse_user_info(token_record: dict[str, Any]) -> dict[str, Any]:
    user_info = token_record.get("userInfo")
    if isinstance(user_info, str):
        user_info = _parse_json_object(user_info)
    return user_info if isinstance(user_info, dict) else {}


def _extract_account(user_info: dict[str, Any]) -> str | None:
    configured_key = settings.PLATFORM_AUTH_USERINFO_ACCOUNT_FIELD or "username"
    account = user_info.get(configured_key) or user_info.get("username") or user_info.get("account")
    if account is None:
        return None
    account_text = str(account).strip()
    return account_text if account_text else None


def _build_platform_identity(token_record: dict[str, Any]) -> PlatformTokenIdentity | None:
    expires_at = _parse_datetime(token_record.get("expiresTime"))
    if expires_at and _is_expired(expires_at):
        return None

    required_user_type = settings.PLATFORM_AUTH_REQUIRE_USER_TYPE
    token_user_type = _to_int(token_record.get("userType"))
    if required_user_type is not None and token_user_type is not None and token_user_type != required_user_type:
        return None

    user_info = _parse_user_info(token_record)
    user_id = _to_int(token_record.get("userId"))
    tenant_id = _to_int(token_record.get("tenantId")) or _to_int(token_record.get("oid"))
    account = _extract_account(user_info)
    nickname = str(user_info.get("nickname") or account or "")

    if user_id is None or tenant_id is None or account is None:
        return None
    return PlatformTokenIdentity(
        user_id=user_id,
        account=account,
        tenant_id=tenant_id,
        nickname=nickname,
        payload=token_record,
    )


async def resolve_platform_token_identity(access_token: str) -> PlatformTokenIdentity | None:
    redis_client = await _get_platform_auth_redis_client()
    if redis_client is None:
        return None

    redis_key = _normalize_redis_key(access_token)
    raw_value = await redis_client.get(redis_key)
    if not raw_value:
        return None
    token_record = _parse_json_object(raw_value)
    if token_record is None:
        return None
    return _build_platform_identity(token_record)
