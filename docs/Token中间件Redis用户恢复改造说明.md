# SQLBot Token中间件对接数据中台 Redis 说明（SQLBot-cx）

本文档对应目录：`/mnt/h/light/project/SQLBot-cx`。

目标：

1. SQLBot 接口使用数据中台 `access_token` 完成鉴权。
2. Token 校验优先从 Redis 读取，不再依赖 SQLBot 本地 JWT。
3. 在 strict 模式下，禁用 SQLBot 本地账号密码登录。

## 1. 改造结论

当前实现支持两种运行模式：

1. 兼容模式：`PLATFORM_AUTH_STRICT_MODE=false`
- chat 路由优先走中台 Redis token
- 其他路径仍可回退 SQLBot 本地 JWT

2. 严格模式：`PLATFORM_AUTH_STRICT_MODE=true`
- 所有 Bearer token 强制走中台 Redis
- 本地 JWT 回退关闭
- `/api/v1/login/access-token` 禁用
- `/api/v1/mcp/mcp_start` 禁用

你当前需求是“去掉 SQLBot 原登录、直接使用中台 token”，应使用严格模式。

## 2. 本次修改文件

1. `backend/apps/system/middleware/auth.py`
- `validateToken` 新增 strict 逻辑
- strict 时仅允许 Redis token 通过
- `UserInfoDTO` 由 Redis token 映射构建并注入 `request.state.current_user`

2. `backend/common/core/config.py`
- 新增 `PLATFORM_AUTH_STRICT_MODE` 配置

3. `backend/apps/system/api/login.py`
- strict 时禁止本地 `/login/access-token`

4. `backend/apps/mcp/mcp.py`
- `get_user` 支持中台 Redis token
- strict 时禁止 `mcp_start`（本地账号密码登录）

5. `frontend/src/router/watch.ts`
- 支持从 URL 注入 token（`x_sqlbot_token/sqlbot_token/token/access_token`）
- 注入后自动写入 `user.token`
- 自动清理 URL 中 token 参数
- 携带 token 访问 `/login` 时自动恢复用户并跳转

## 3. 数据流（严格模式）

1. 前端携带 `X-SQLBOT-TOKEN: Bearer <access_token>`（或 `Authorization`）
2. 中间件读取 token
3. 按 key `oauth2_access_token:{token}` 查询 Redis
4. 命中后解析字段：`userId / userInfo / tenantId / expiresTime`
5. 构建 `UserInfoDTO`，写入 `request.state.current_user`
6. 接口继续执行业务逻辑
7. Redis 未命中或过期：直接 401（不回退本地 JWT）

## 4. Redis token 字段映射

中台字段到 SQLBot 用户字段映射：

1. `userId` -> `id`
2. `userInfo.username`（可配置）-> `account`
3. `tenantId`（缺失则回退 `oid`）-> `oid`
4. `userInfo.nickname` -> `name`

补全默认值：

1. `origin=1`
2. `language=zh-CN`
3. `email=<account>@platform.local`
4. `isAdmin`：`account=admin` 或 `userId=1` 时为 true

## 5. 环境配置

后端 `.env`（strict 推荐）：

```env
PLATFORM_AUTH_ENABLED=true
PLATFORM_AUTH_STRICT_MODE=true
PLATFORM_AUTH_REDIS_URL=redis://127.0.0.1:6379/1
PLATFORM_AUTH_REDIS_KEY_PREFIX=oauth2_access_token:%s
PLATFORM_AUTH_USERINFO_ACCOUNT_FIELD=username
PLATFORM_AUTH_ACCEPT_AUTHORIZATION_HEADER=true
# 可选：限制用户类型
# PLATFORM_AUTH_REQUIRE_USER_TYPE=2
```

说明：

1. 你的 Redis token 在 `db1`，URL 需带 `/1`。
2. 如需密码：`redis://:<password>@host:6379/1`。

## 6. 前端接入方式（和中台联动）

SQLBot 前端已支持 URL 注入 token，示例：

```text
http://100.66.219.51:8000/#/chat?sqlbot_token=<access_token>
```

也支持：

1. `x_sqlbot_token`
2. `token`
3. `access_token`

如果值带 `Bearer ` 前缀也可识别。

注入后效果：

1. 自动保存到 `user.token`
2. 自动请求 `/api/v1/user/info` 恢复用户
3. 自动离开登录页进入业务页
4. 自动清理地址栏中的 token 参数

## 7. 调用验证

### 7.1 Redis 验证

```bash
redis-cli -h 127.0.0.1 -p 6379 -n 1 --scan --pattern 'oauth2_access_token:*' | head
redis-cli -h 127.0.0.1 -p 6379 -n 1 GET 'oauth2_access_token:<token>'
```

### 7.2 接口验证

```bash
curl -i 'http://100.66.219.51:8000/api/v1/user/info' \
  -H 'X-SQLBOT-TOKEN: Bearer <token>'
```

预期：

1. 返回 200
2. `id/account/oid` 与 Redis token 一致

### 7.3 本地登录禁用验证（strict 模式）

```bash
curl -i 'http://100.66.219.51:8000/api/v1/login/access-token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'username=xxx&password=xxx'
```

预期：`403`，提示本地登录已禁用。

## 8. 行为说明（你关心的问题）

“改完后会不会还用 SQLBot 本身登录？”

1. strict=false：会。Redis 不命中时仍可能走本地 JWT。
2. strict=true：不会。Bearer token 只认中台 Redis token，本地登录入口已禁用。

## 9. 代码备份

本次改造前备份目录：

`backup/token_platform_strict_20260303_010113`

包含以下备份文件：

1. `backup/token_platform_strict_20260303_010113/backend/apps/system/middleware/auth.py.bak`
2. `backup/token_platform_strict_20260303_010113/backend/common/core/config.py.bak`
3. `backup/token_platform_strict_20260303_010113/backend/apps/system/api/login.py.bak`
4. `backup/token_platform_strict_20260303_010113/backend/apps/mcp/mcp.py.bak`
5. `backup/token_platform_strict_20260303_010113/frontend/src/router/watch.ts.bak`

## 10. 常见问题

1. 401 且 Redis 有 key
- 检查是否使用了错误 DB（0/1）
- 检查 key 前缀是否为 `oauth2_access_token:%s`
- 检查 token 是否过期（`expiresTime`）

2. 前端已打开但仍回登录页
- 检查 URL 是否传了 token 参数
- 检查请求头是否发出 `X-SQLBOT-TOKEN: Bearer ...`
- 检查 `/api/v1/user/info` 是否返回 200

3. 非 chat 接口仍失败
- strict 模式下要求所有 Bearer token 都是中台 Redis token
- 若中台 token 无效会直接 401，不会回退本地 JWT
