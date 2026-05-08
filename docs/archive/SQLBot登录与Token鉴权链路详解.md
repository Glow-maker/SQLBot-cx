# SQLBot登录与 Token 鉴权链路详解

## 1. 文档目的

本文基于当前 `SQLBot-cx` 代码，说明你从 `http://100.66.219.51:9473` 打开前端后，登录到后续接口调用的完整链路，并重点回答以下问题：

1. 登录后拿到的 token，后续接口是否都使用它？
2. 是否已经替代原 SQLBot 本地 JWT token？
3. 对 SQLBot 其他功能是否有影响？
4. token 验证是否与 Redis 交互？是否每次请求都查 Redis？

---

## 2. 当前实现涉及的核心文件

前端：

- `frontend/src/api/login.ts`
- `frontend/src/stores/user.ts`
- `frontend/src/utils/request.ts`
- `frontend/src/router/watch.ts`

后端：

- `backend/apps/system/api/login.py`
- `backend/apps/system/middleware/auth.py`
- `backend/common/core/token_resolver.py`
- `backend/common/core/deps.py`
- `backend/apps/system/api/user.py`
- `backend/common/core/config.py`

---

## 3. 从进入 9473 到登录成功的实际流程

## 3.1 打开页面与路由守卫

1. 你访问 `http://100.66.219.51:9473`，浏览器加载前端静态资源。
2. 路由守卫读取本地缓存 `user.token`（`wsCache`）。
3. 如果 URL 上带有 `token/access_token` 参数，也会被提取并写入本地缓存（支持外部带 token 进入）。
4. 如果已有 token，会先请求 `/api/v1/user/info` 恢复用户态；失败则清空并回登录页。

对应代码：

- `frontend/src/router/watch.ts:23`
- `frontend/src/router/watch.ts:41`
- `frontend/src/router/watch.ts:104`

## 3.2 账号密码登录请求

点击登录后前端会：

1. 对用户名、密码做前端加密（`LicenseGenerator.sqlbotEncrypt`）。
2. 调用 `POST /api/v1/login/access-token`，`Content-Type: application/x-www-form-urlencoded`。

对应代码：

- `frontend/src/api/login.ts:5`
- `frontend/src/api/login.ts:11`

## 3.3 SQLBot 后端登录接口行为（已改造）

后端 `login.py` 逻辑：

1. 先解密前端传入的账号密码（`sqlbot_decrypt`）。
2. 若 `PLATFORM_AUTH_LOGIN_PROXY_ENABLED=true`，走“中台登录代理”：
   - 组装请求体：`tenantName + username + password + rememberMe`
   - 组装请求头：`tenant-id`（由 `PLATFORM_AUTH_FIXED_TENANT_ID` 或查询租户接口得到）
   - 调中台接口：`/admin-api/system/auth/login`
3. 成功后把中台 `accessToken` 返回给前端，映射为：
   - `access_token`
   - `token_type = bearer`
   - `platform_info`（包含 userId / refreshToken / expiresTime）

对应代码：

- `backend/apps/system/api/login.py:74`
- `backend/apps/system/api/login.py:90`
- `backend/apps/system/api/login.py:120`

---

## 4. 登录成功后，后续接口如何携带 token

前端将登录返回的 `access_token` 写入缓存 `user.token`，随后每个请求默认自动加头：

- `X-SQLBOT-TOKEN: Bearer <token>`

对应代码：

- `frontend/src/stores/user.ts:85`
- `frontend/src/stores/user.ts:147`
- `frontend/src/utils/request.ts:99`

所以，正常业务请求（如 `/api/v1/user/info`、`/api/v1/chat/...`）后续都依赖这个 token。

---

## 5. 后端如何校验 token（中间件流程）

`TokenMiddleware` 是统一入口，核心顺序如下：

1. 白名单路径直接放行（例如 `/login/*`、静态资源等）。
2. 若是 Assistant token 或 Ask token，走对应专用校验逻辑。
3. 其他请求走 `validateToken()`：
   - 解析 `Bearer token`
   - 按配置决定是否走中台 Redis 恢复用户
   - 校验成功后把用户写入 `request.state.current_user`
4. 业务接口通过 `CurrentUser` 依赖直接拿 `current_user`（例如 `/user/info` 原样返回）。

对应代码：

- `backend/apps/system/middleware/auth.py:32`
- `backend/apps/system/middleware/auth.py:121`
- `backend/common/core/deps.py:18`
- `backend/apps/system/api/user.py:41`

---

## 6. 是否“直接替代了原 SQLBot token”？

结论分配置看：

## 6.1 你当前推荐配置（已使用）

- `PLATFORM_AUTH_LOGIN_PROXY_ENABLED=true`
- `PLATFORM_AUTH_ENABLED=true`
- `PLATFORM_AUTH_STRICT_MODE=true`

在这组配置下：

1. 登录发 token 由中台负责（SQLBot 只做代理）。
2. 鉴权只接受“中台 token（Redis 可查到）”。
3. SQLBot 本地 JWT 登录链路不会被使用（严格模式下禁止回退）。

可以理解为：**用户态 token 已经被中台 token 替代**。

## 6.2 兼容模式（非 strict）说明

如果 `PLATFORM_AUTH_STRICT_MODE=false`，当前代码对非 chat 路由可能回退本地 JWT，这会与中台的“随机串 accessToken”产生冲突（常见报错 `Not enough segments`）。

---

## 7. token 校验是否每次都查 Redis？

在 `strict=true` 且普通用户 Bearer 请求场景下，**是的，当前实现是每次请求一次 Redis GET**：

1. Redis key：`oauth2_access_token:<accessToken>`（可通过配置前缀调整）
2. 读取 value 后解析 JSON
3. 校验过期时间 `expiresTime`
4. 校验 `userType`（如果配置了 `PLATFORM_AUTH_REQUIRE_USER_TYPE`）
5. 组装 `UserInfoDTO`，写入 `request.state.current_user`

对应代码：

- `backend/common/core/token_resolver.py:169`
- `backend/common/core/token_resolver.py:137`
- `backend/apps/system/middleware/auth.py:138`

## 重要差异

你当前 SQLBot 实现是“**Redis-only 校验**”，没有在 SQLBot 内做中台那种 “Redis miss -> MySQL 回退再回填 Redis” 逻辑。  
因此 Redis 不可用或 key 缺失时会直接鉴权失败（401）。

---

## 8. 对 SQLBot 其他功能的影响

## 8.1 基本不受影响的部分

1. 绝大多数依赖 `CurrentUser` 的接口仍可工作，因为用户对象已在中间件恢复。
2. chat、workspace、系统管理等接口读取的是 `current_user.id/account/oid`，这些字段已映射。

## 8.2 需要注意的行为变化

1. `UserInfoDTO` 部分字段为映射/默认值（例如 email 会是 `account@platform.local`）。
2. `isAdmin` 当前按 `account=admin` 或 `user_id=1` 推断，不等于中台完整角色体系。
3. 当前前端普通登录流程只保存 `access_token`，未消费 `platform_info` 做 refresh；即暂未做自动刷新链路。
4. `/login/logout` 目前不是完整中台登出代理链路，需按需求再补。

---

## 9. 9473 登录时序图（当前实现）

```text
浏览器(9473)
  -> 前端登录页: 输入账号密码
  -> SQLBot后端 /api/v1/login/access-token: form提交(加密账号密码)

SQLBot后端
  -> 解密用户名密码
  -> 中台 /admin-api/system/auth/login: JSON + Header tenant-id

中台
  -> 校验租户/账号密码
  -> 生成 accessToken/refreshToken
  -> accessToken 写入 Redis
  -> 返回 token 数据给 SQLBot

SQLBot后端
  -> 返回 access_token 给前端

前端
  -> 保存 user.token
  -> 后续每个请求都带 X-SQLBOT-TOKEN: Bearer <token>

每次业务请求到 SQLBot后端
  -> TokenMiddleware 解析 Bearer
  -> Redis GET oauth2_access_token:<token>
  -> 通过则构建 current_user，接口继续执行
  -> 不通过返回401
```

---

## 10. 你关心问题的直接回答

1. 登录拿到 token 后续服务都是用这个 token 吗？  
是。普通用户请求默认都使用这个 token 放在 `X-SQLBOT-TOKEN`。

2. 它是否代替原 SQLBot token？  
在你当前配置（proxy+strict）下，是。原本 SQLBot 本地 JWT 链路保留在代码里，但不会走到。

3. SQLBot 其他功能有影响吗？  
核心功能可继续用；影响主要在“用户属性来源变成中台 token 映射值”，以及“管理员判定/刷新登出策略”需要后续完善。

4. token 校验是否和 Redis 交互？每次都交互吗？  
是。当前实现为请求级 Redis 校验，普通 Bearer 请求基本都是每次一次 Redis GET。

