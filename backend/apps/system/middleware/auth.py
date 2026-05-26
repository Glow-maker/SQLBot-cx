
import base64
import json
from typing import Optional
from fastapi import Request
from fastapi.responses import JSONResponse
import jwt
from sqlmodel import Session
from starlette.middleware.base import BaseHTTPMiddleware
from apps.system.crud.apikey_manage import get_api_key
from apps.system.models.system_model import ApiKeyModel, AssistantModel
from common.core.db import engine 
from apps.system.crud.assistant import get_assistant_info, get_assistant_user
from apps.system.crud.user import get_user_by_account, get_user_info
from apps.system.schemas.system_schema import AssistantHeader, UserInfoDTO
from common.core import security
from common.core.config import settings
from common.core.schemas import TokenPayload
from common.core.token_resolver import PlatformTokenIdentity, resolve_platform_token_identity
from common.utils.locale import I18n
from common.utils.utils import SQLBotLogUtil, get_origin_from_referer
from common.utils.whitelist import whiteUtils
from fastapi.security.utils import get_authorization_scheme_param
from common.core.deps import get_i18n
class TokenMiddleware(BaseHTTPMiddleware):
    
    
    
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        
        if self.is_options(request) or whiteUtils.is_whitelisted(request.url.path):
            return await call_next(request)
        assistantTokenKey = settings.ASSISTANT_TOKEN_KEY
        assistantToken = request.headers.get(assistantTokenKey)
        askToken = request.headers.get("X-SQLBOT-ASK-TOKEN")
        trans = await get_i18n(request)
        if askToken:
            validate_pass, data = await self.validateAskToken(askToken, trans)
            if validate_pass:
                request.state.current_user = data
                return await call_next(request)
            message = trans('i18n_permission.authenticate_invalid', msg = data)
            return JSONResponse(message, status_code=401, headers={"Access-Control-Allow-Origin": "*"})
        #if assistantToken and assistantToken.lower().startswith("assistant "):
        if assistantToken:
            validator: tuple[any] = await self.validateAssistant(assistantToken, trans)
            if validator[0]:
                request.state.current_user = validator[1]
                if request.state.current_user and trans.lang:
                    request.state.current_user.language = trans.lang
                request.state.assistant = validator[2]
                origin = request.headers.get("X-SQLBOT-HOST-ORIGIN") or get_origin_from_referer(request)
                if origin and validator[2]:
                    request.state.assistant.request_origin = origin
                return await call_next(request)
            message = trans('i18n_permission.authenticate_invalid', msg = validator[1])
            return JSONResponse(message, status_code=401, headers={"Access-Control-Allow-Origin": "*"})
        #validate pass
        tokenkey = settings.TOKEN_KEY
        token = request.headers.get(tokenkey)
        if not token and settings.PLATFORM_AUTH_ACCEPT_AUTHORIZATION_HEADER:
            token = request.headers.get("Authorization")
        validate_pass, data = await self.validateToken(token, trans, request.url.path)
        if validate_pass:
            request.state.current_user = data
            # 主线二 Phase 2a：当中台数据源接入开启时，注入虚拟 assistant 到 request.state，
            # 让下游 chat 主流程通过现有 AssistantOutDs 链路从中台 data 服务远程拉 ds，
            # chat 代码无需任何分支改造。
            self._inject_platform_assistant_if_enabled(request, token)
            return await call_next(request)
        
        message = trans('i18n_permission.authenticate_invalid', msg = data)
        return JSONResponse(message, status_code=401, headers={"Access-Control-Allow-Origin": "*"})
    
    def is_options(self, request: Request):
        return request.method == "OPTIONS"
    
    async def validateAskToken(self, askToken: Optional[str], trans: I18n):
        if not askToken:
            return False, f"Miss Token[X-SQLBOT-ASK-TOKEN]!"
        schema, param = get_authorization_scheme_param(askToken)
        if schema.lower() != "sk":
            return False, f"Token schema error!"
        try: 
            payload = jwt.decode(
                param, options={"verify_signature": False, "verify_exp": False}, algorithms=[security.ALGORITHM]
            )
            access_key = payload.get('access_key', None)
            
            if not access_key:
                return False, f"Miss access_key payload error!"
            with Session(engine) as session:
                api_key_model = await get_api_key(session, access_key)
                api_key_model = ApiKeyModel.model_validate(api_key_model) if api_key_model else None
                if not api_key_model:
                    return False, f"Invalid access_key!"
                if not api_key_model.status:
                    return False, f"Disabled access_key!"
                payload = jwt.decode(
                    param, api_key_model.secret_key, algorithms=[security.ALGORITHM]
                )
                uid = api_key_model.uid
                session_user = await get_user_info(session = session, user_id = uid)
                if not session_user:
                    message = trans('i18n_not_exist', msg = trans('i18n_user.account'))
                    raise Exception(message)
                session_user = UserInfoDTO.model_validate(session_user)
                if session_user.status != 1:
                    message = trans('i18n_login.user_disable', msg = trans('i18n_concat_admin'))
                    raise Exception(message)
                if not session_user.oid or session_user.oid == 0:
                    message = trans('i18n_login.no_associated_ws', msg = trans('i18n_concat_admin'))
                    raise Exception(message)
                return True, session_user
        except Exception as e:
            msg = str(e)
            SQLBotLogUtil.exception(f"Token validation error: {msg}")
            if 'expired' in msg:
                return False, jwt.ExpiredSignatureError(trans('i18n_permission.token_expired')) 
            return False, e
    
    async def validateToken(self, token: Optional[str], trans: I18n, request_path: Optional[str] = None):
        if not token:
            if settings.PLATFORM_AUTH_ACCEPT_AUTHORIZATION_HEADER:
                return False, f"Miss Token[{settings.TOKEN_KEY} or Authorization]!"
            return False, f"Miss Token[{settings.TOKEN_KEY}]!"
        schema, param = get_authorization_scheme_param(token)
        if schema.lower() != "bearer":
            return False, f"Token schema error!"
        try:
            # 兼容模式：仅 chat 路由优先走中台 Redis token
            # strict 模式：所有 bearer token 都强制走中台 Redis token
            platform_auth_enabled = settings.PLATFORM_AUTH_ENABLED
            platform_auth_strict = settings.PLATFORM_AUTH_STRICT_MODE
            should_try_platform = platform_auth_enabled and (
                platform_auth_strict or self._is_chat_request(request_path)
            )

            if should_try_platform:
                platform_identity = await resolve_platform_token_identity(param)
                if platform_identity:
                    return True, self._build_user_from_platform_token(platform_identity)
                if platform_auth_strict:
                    local_admin_user = await self._try_local_admin_user(param, trans)
                    if local_admin_user:
                        return True, local_admin_user
                    return False, "Invalid or expired platform access token!"

            # strict 模式下不允许回退本地 JWT
            if platform_auth_strict:
                local_admin_user = await self._try_local_admin_user(param, trans)
                if local_admin_user:
                    return True, local_admin_user
                return False, "Platform auth strict mode is enabled, local JWT token is disabled!"

            # 兼容模式下保留 SQLBot 原 JWT 逻辑
            session_user = await self._build_user_from_local_jwt(param, trans)
            return True, session_user
        except Exception as e:
            msg = str(e)
            SQLBotLogUtil.exception(f"Token validation error: {msg}")
            if 'expired' in msg:
                return False, jwt.ExpiredSignatureError(trans('i18n_permission.token_expired')) 
            return False, e

    def _get_local_admin_accounts(self) -> set[str]:
        raw_accounts = settings.PLATFORM_AUTH_LOCAL_ADMIN_ACCOUNTS or ""
        return {item.strip().lower() for item in str(raw_accounts).split(",") if item.strip()}

    def _is_local_admin_account(self, account: Optional[str]) -> bool:
        if not settings.PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN:
            return False
        if not account:
            return False
        return str(account).strip().lower() in self._get_local_admin_accounts()

    async def _build_user_from_local_jwt(self, token_value: str, trans: I18n) -> UserInfoDTO:
        payload = jwt.decode(
            token_value, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
        with Session(engine) as session:
            session_user = await get_user_info(session=session, user_id=token_data.id)
            if not session_user:
                message = trans('i18n_not_exist', msg=trans('i18n_user.account'))
                raise Exception(message)
            session_user = UserInfoDTO.model_validate(session_user)
            if session_user.status != 1:
                message = trans('i18n_login.user_disable', msg=trans('i18n_concat_admin'))
                raise Exception(message)
            if not session_user.oid or session_user.oid == 0:
                message = trans('i18n_login.no_associated_ws', msg=trans('i18n_concat_admin'))
                raise Exception(message)
            return session_user

    async def _try_local_admin_user(self, token_value: str, trans: I18n) -> Optional[UserInfoDTO]:
        if not settings.PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN:
            return None
        try:
            local_user = await self._build_user_from_local_jwt(token_value, trans)
        except Exception:
            return None
        if self._is_local_admin_account(local_user.account):
            return local_user
        return None

    def _is_chat_request(self, request_path: Optional[str]) -> bool:
        if not request_path:
            return False
        chat_prefix = f"{settings.API_V1_STR}/chat"
        return request_path == chat_prefix or request_path.startswith(f"{chat_prefix}/")

    def _inject_platform_assistant_if_enabled(self, request: Request, token_value: Optional[str]) -> None:
        """
        主线二 Phase 2a：注入"虚拟 assistant"到 request.state，让 chat 主流程通过现有
        AssistantOutDs 链路从中台 data 服务远程拉数据源（无需改 chat 任何分支）。

        触发条件：
        - settings.PLATFORM_DATASOURCE_ENABLED = true
        - settings.PLATFORM_DATASOURCE_BASE_URL 已配置
        - 已成功解析出 current_user（说明已通过中台或本地认证）
        - request.state.assistant 还未被赋值（避免覆盖业务真实 assistant 链路）

        构造的虚拟 AssistantHeader：
        - type=1（落入 dynamic_ds_types，触发 AssistantOutDsFactory 路径）
        - configuration: {"endpoint": <list_path>, "timeout": ...}
        - certificate: [{"target":"header","key":"Authorization","value":"Bearer <token>"},
                        {"target":"header","key":"tenant-id","value":"<oid>"}]
        - domain: PLATFORM_DATASOURCE_BASE_URL（AssistantOutDs 拼接相对 endpoint 时用）
        """
        if not settings.PLATFORM_DATASOURCE_ENABLED:
            return
        base_url = settings.PLATFORM_DATASOURCE_BASE_URL
        if not base_url:
            return
        # 已有真实 assistant（如 X-SQLBOT-ASSISTANT-TOKEN 路径），不覆盖
        if getattr(request.state, "assistant", None):
            return
        current_user: Optional[UserInfoDTO] = getattr(request.state, "current_user", None)
        if current_user is None:
            return
        if not token_value:
            return
        scheme, param = get_authorization_scheme_param(token_value)
        if not param:
            param = token_value
        if scheme and scheme.lower() == "bearer":
            authorization_value = f"Bearer {param}"
        else:
            authorization_value = token_value

        configuration = json.dumps({
            "endpoint": settings.PLATFORM_DATASOURCE_LIST_PATH,
            "timeout": int(settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS),
            "oid": int(current_user.oid) if current_user.oid is not None else 1,
            # 标记位：让上层（如 cache）知道这是中台虚拟 assistant，不与本地 assistant 混淆
            "platform_virtual": True,
        })
        certificate = json.dumps([
            {"target": "header", "key": "Authorization", "value": authorization_value},
            {"target": "header", "key": settings.PLATFORM_AUTH_TENANT_ID_HEADER,
             "value": str(int(current_user.oid)) if current_user.oid is not None else ""},
        ])
        try:
            virtual_assistant = AssistantHeader(
                id=0,  # 虚拟 ID；用于区分真实 assistant
                name="platform-virtual",
                domain=str(base_url).rstrip("/"),
                type=1,  # 落入 dynamic_ds_types=[1,3]
                configuration=configuration,
                certificate=certificate,
                oid=int(current_user.oid) if current_user.oid is not None else 1,
                online=True,
            )
            request.state.assistant = virtual_assistant
        except Exception as exc:
            # 注入失败不阻塞主流程，仅记日志，下游 select_datasource 会回退本地 ds
            SQLBotLogUtil.exception(f"Inject platform virtual assistant failed: {exc}")

    def _build_user_from_platform_token(self, identity: PlatformTokenIdentity) -> UserInfoDTO:
        # 中台 token 恢复用户：chat 仅依赖 id/account/oid，其他字段使用安全默认值
        nickname = identity.nickname or identity.account
        is_admin = str(identity.account).lower() == "admin" or int(identity.user_id) == 1
        return UserInfoDTO(
            id=int(identity.user_id),
            account=str(identity.account),
            name=str(nickname),
            oid=int(identity.tenant_id),
            email=f"{identity.account}@platform.local",
            status=1,
            origin=1,
            language="zh-CN",
            weight=1 if is_admin else 0,
            isAdmin=is_admin,
        )
            
    
    async def validateAssistant(self, assistantToken: Optional[str], trans: I18n) -> tuple[any]:
        if not assistantToken:
            return False, f"Miss Token[{settings.TOKEN_KEY}]!"
        schema, param = get_authorization_scheme_param(assistantToken)
        
        
        try:
            if schema.lower() == 'embedded':
                return await self.validateEmbedded(param, trans)
            if schema.lower() != "assistant":
                return False, f"Token schema error!" 
            payload = jwt.decode(
                param, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
            )
            token_data = TokenPayload(**payload)
            if not payload['assistant_id']:
                return False, f"Miss assistant payload error!"
            with Session(engine) as session:
                """ session_user = await get_user_info(session = session, user_id = token_data.id)
                session_user = UserInfoDTO.model_validate(session_user) """
                session_user = get_assistant_user(id = token_data.id)
                assistant_info = await get_assistant_info(session=session, assistant_id=payload['assistant_id'])
                assistant_info = AssistantModel.model_validate(assistant_info)
                assistant_info = AssistantHeader.model_validate(assistant_info.model_dump(exclude_unset=True))
                session_user.oid = int(assistant_info.oid)
                        
                return True, session_user, assistant_info
        except Exception as e:
            SQLBotLogUtil.exception(f"Assistant validation error: {str(e)}")
            # Return False and the exception message
            return False, e
    
    async def validateEmbedded(self, param: str, trans: I18n) -> tuple[any]:
        try: 
            # WARNING: Signature verification is disabled for embedded tokens
            # This is a security risk and should only be used if absolutely necessary
            # Consider implementing proper signature verification with a shared secret
            payload: dict = jwt.decode(
                param,
                options={"verify_signature": False, "verify_exp": False},
                algorithms=[security.ALGORITHM]
            )
            app_key = payload.get('appId', '')
            embeddedId = payload.get('embeddedId', None)
            if not embeddedId:
                embeddedId = xor_decrypt(app_key)
            if not payload['account']:
                return False, f"Miss account payload error!"
            account = payload['account']
            with Session(engine) as session:
                assistant_info = await get_assistant_info(session=session, assistant_id=embeddedId)
                assistant_info = AssistantModel.model_validate(assistant_info)
                payload = jwt.decode(
                    param, assistant_info.app_secret, algorithms=[security.ALGORITHM]
                )
                assistant_info = AssistantHeader.model_validate(assistant_info.model_dump(exclude_unset=True))
                """ session_user = await get_user_info(session = session, user_id = token_data.id)
                session_user = UserInfoDTO.model_validate(session_user) """
                session_user = get_user_by_account(session = session, account=account)
                if not session_user:
                    message = trans('i18n_not_exist', msg = trans('i18n_user.account'))
                    raise Exception(message)
                session_user = await get_user_info(session = session, user_id = session_user.id)
                
                session_user = UserInfoDTO.model_validate(session_user)
                if session_user.status != 1:
                    message = trans('i18n_login.user_disable', msg = trans('i18n_concat_admin'))
                    raise Exception(message)
                if not session_user.oid or session_user.oid == 0:
                    message = trans('i18n_login.no_associated_ws', msg = trans('i18n_concat_admin'))
                    raise Exception(message)
                if session_user.oid:
                    assistant_info.oid = int(session_user.oid)
                return True, session_user, assistant_info
        except Exception as e:
            SQLBotLogUtil.exception(f"Embedded validation error: {str(e)}")
            # Return False and the exception message
            return False, e
    
def xor_decrypt(encrypted_str: str, key: int = 0xABCD1234) -> int:
    encrypted_bytes = base64.urlsafe_b64decode(encrypted_str)
    hex_str = encrypted_bytes.hex()
    encrypted_num = int(hex_str, 16)
    return encrypted_num ^ key
