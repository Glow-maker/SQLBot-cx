from datetime import timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm

from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.config import settings
from common.core.deps import SessionDep, Trans
from common.core.schemas import Token
from common.core.security import create_access_token
from common.utils.crypto import sqlbot_decrypt
from common.utils.utils import SQLBotLogUtil
from sqlbot_xpack.authentication.manage import logout as xpack_logout

from apps.system.schemas.logout_schema import LogoutSchema
from apps.system.schemas.system_schema import BaseUserDTO
from ..crud.user import authenticate

router = APIRouter(tags=["login"], prefix="/login")


def _build_platform_auth_url(path: str) -> str:
    base_url = settings.PLATFORM_AUTH_BASE_URL
    if not base_url:
        raise HTTPException(status_code=500, detail="PLATFORM_AUTH_BASE_URL is not configured.")
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


async def _platform_login(
    username: str,
    password: str,
    trans: Trans,
) -> Token:
    payload: dict[str, Any] = {
        "username": username,
        "password": password,
        "rememberMe": settings.PLATFORM_AUTH_LOGIN_REMEMBER_ME,
    }
    if settings.PLATFORM_AUTH_FIXED_TENANT_NAME:
        payload["tenantName"] = settings.PLATFORM_AUTH_FIXED_TENANT_NAME

    login_url = _build_platform_auth_url(settings.PLATFORM_AUTH_LOGIN_PATH)
    timeout = settings.PLATFORM_AUTH_HTTP_TIMEOUT_SECONDS
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(login_url, json=payload)
    except httpx.HTTPError as exc:
        SQLBotLogUtil.exception(f"Platform login request failed: {exc}")
        raise HTTPException(status_code=502, detail="Platform auth service is unavailable.") from exc

    response_text = response.text
    try:
        body = response.json()
    except ValueError as exc:
        SQLBotLogUtil.exception(f"Platform login response parse failed: {response_text}")
        raise HTTPException(status_code=502, detail="Invalid platform auth response.") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="Invalid platform auth response structure.")

    code = body.get("code")
    if code != 0:
        msg = body.get("msg") or trans("i18n_login.account_pwd_error")
        raise HTTPException(status_code=401, detail=str(msg))

    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Invalid platform auth response data.")

    access_token = data.get("accessToken")
    if not access_token:
        raise HTTPException(status_code=502, detail="Platform auth missing accessToken.")

    platform_info = {
        "userId": data.get("userId"),
        "refreshToken": data.get("refreshToken"),
        "expiresTime": data.get("expiresTime"),
    }
    return Token(access_token=str(access_token), token_type="bearer", platform_info=platform_info)


@router.post("/access-token")
@system_log(LogConfig(
    operation_type=OperationType.LOGIN,
    module=OperationModules.USER,
    result_id_expr="id"
))
async def local_login(
    session: SessionDep,
    trans: Trans,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    origin_account = await sqlbot_decrypt(form_data.username)
    origin_pwd = await sqlbot_decrypt(form_data.password)

    if settings.PLATFORM_AUTH_LOGIN_PROXY_ENABLED:
        return await _platform_login(origin_account, origin_pwd, trans)

    if settings.PLATFORM_AUTH_ENABLED and settings.PLATFORM_AUTH_STRICT_MODE:
        raise HTTPException(status_code=403, detail="Local login is disabled in platform auth strict mode.")

    user: BaseUserDTO = authenticate(session=session, account=origin_account, password=origin_pwd)
    if not user:
        raise HTTPException(status_code=400, detail=trans('i18n_login.account_pwd_error'))
    if not user.oid or user.oid == 0:
        raise HTTPException(status_code=400, detail=trans('i18n_login.no_associated_ws', msg = trans('i18n_concat_admin')))
    if user.status != 1:
        raise HTTPException(status_code=400, detail=trans('i18n_login.user_disable', msg = trans('i18n_concat_admin')))
    if user.origin is not None and user.origin != 0:
        raise HTTPException(status_code=400, detail=trans('i18n_login.origin_error'))
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    user_dict = user.to_dict()
    return Token(access_token=create_access_token(
        user_dict, expires_delta=access_token_expires
    ))

@router.post("/logout")    
async def logout(session: SessionDep, request: Request, dto: LogoutSchema):
    if dto.origin != 0:
        return await xpack_logout(session, request, dto)
    return None
