# SQLBot-cx 后端 token 认证改造校验报告

> 校验对象：`H:\light\project\SQLBot-cx\backend`  
> 需求来源：`docs/requirement/Token 认证机制与用户信息获取指南 (1).md`、`docs/requirement/后端改造方案.md`  
> 校验方式：代码走查 + mock 契约自验 + 当前运行后端 HTTP 探测 + Redis 短 TTL mock token 端到端验证

## 1. 结论

SQLBot 后端的 **token 认证主线已经完成并在当前运行实例上生效**。

具体表现：

- 已支持从 `Authorization: Bearer <token>` 和 `X-SQLBOT-TOKEN: Bearer <token>` 读取 token。
- 已按中台 OAuth2 Redis 结构读取 `oauth2_access_token:<token>`，解析 `userId / tenantId / userType / userInfo / expiresTime`。
- 已能把中台 token 恢复成 SQLBot `current_user`，chat 接口能基于该用户态正常放行。
- 当前 `.env` 已开启 `PLATFORM_AUTH_ENABLED=true`、`PLATFORM_AUTH_STRICT_MODE=true`，伪造或过期 token 会被 401 拒绝。
- mock 自验通过：认证主线 `42/42`，数据源与权限主线 `24/24`。

但 **数据源改造与表权限链路尚未完成端到端联调**：

- 当前 `.env` 中 `PLATFORM_DATASOURCE_ENABLED=false`，运行实例不会注入虚拟 Assistant，也不会启用远程数据源和权限 Gate。
- 用户给出的数据中台后端 `http://127.0.0.1:48080` 对 `GET /openapi/sqlbot/datasources/query` 返回业务 `code=404`，说明该接口当前未就绪或路径不匹配。
- 当前 `.env` 中 `PLATFORM_AUTH_BASE_URL=http://127.0.0.1:8080`，但本机 8080 未连通；登录代理测试返回 502，登录代理链路需要修正地址或启动对应网关。

## 2. 需求对照

| 需求点 | 实现状态 | 证据 |
|---|---|---|
| SQLBot token 中间件兼容中台 token | 已完成 | `apps/system/middleware/auth.py` 从 `Authorization` 或 `X-SQLBOT-TOKEN` 取 Bearer token，并调用 `resolve_platform_token_identity` |
| 从 Redis 获取用户信息 | 已完成 | `common/core/token_resolver.py` 使用 `PLATFORM_AUTH_REDIS_URL` 和 `PLATFORM_AUTH_REDIS_KEY_PREFIX` 读取 token JSON |
| 恢复 chat 需要的用户态 | 已完成 | `_build_user_from_platform_token` 生成 `UserInfoDTO(id/account/oid/name/status/isAdmin)` |
| 严格模式下拒绝非法 token | 已完成 | 当前运行实例对 missing / invalid / expired / wrong userType 均返回 401 |
| 本地 admin 运维入口 | 已完成 | `PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN=true`，本地 admin 白名单逻辑存在 |
| 登录代理到中台 | 代码已实现，当前环境未通过 | `/api/v1/login/access-token` 加密假账号测试返回 502；`PLATFORM_AUTH_BASE_URL` 指向 8080，用户提供的中台入口是 48080 |
| chat/start 支持不传 datasource | 代码已实现，当前环境未启用 | `PLATFORM_DATASOURCE_ENABLED=true` 时 `require_datasource=false`；当前 `.env` 为 false |
| 远程数据源获取 | SQLBot 侧骨架完成，中台接口未就绪 | mock 通过；真实 `48080/openapi/sqlbot/datasources/query` 返回 `{"code":404,...}` |
| 单表/表权限限制 | SQLBot 侧双 Gate 骨架完成，当前未启用 | `PLATFORM_DATASOURCE_ENABLED=false`；真实权限接口尚未验证 |

## 3. 关键实现证据

### 3.1 Token 中间件

- `apps/system/middleware/auth.py`
  - 读取 `X-SQLBOT-TOKEN`，若为空且允许兼容，则读取 `Authorization`。
  - strict 模式或 chat 路由下优先走中台 Redis token。
  - strict 模式下 Redis miss / expired / userType 不匹配会拒绝。
  - 解析成功后写入 `request.state.current_user`。

### 3.2 Redis token resolver

- `common/core/token_resolver.py`
  - Redis key 模板：`oauth2_access_token:%s`。
  - 兼容普通 JSON 与二次 JSON 编码。
  - 校验 `expiresTime`。
  - 可按 `PLATFORM_AUTH_REQUIRE_USER_TYPE=2` 限制用户类型。
  - 支持 `tenantId`，无 `tenantId` 时回退 `oid`。

### 3.3 数据源与权限骨架

- `apps/chat/api/chat.py`
  - `PLATFORM_DATASOURCE_ENABLED=true` 时 `chat/start` 不再强制要求 datasource。
- `apps/system/middleware/auth.py`
  - 可注入虚拟 `AssistantHeader`，把 `Authorization` 与 `tenant-id` 透传给中台数据源服务。
- `common/utils/platform_permission.py`
  - 提供表权限校验客户端，网络异常、5xx、非法 JSON 都 fail-closed。
- `apps/chat/task/llm.py`
  - Gate 1：SQL 生成后校验。
  - Gate 2：执行 SQL 前用最终 SQL 再校验。

## 4. Mock 验证结果

运行环境使用项目实际后端解释器：

```powershell
D:\Anaconda3\envs\sqlbot\python.exe -X utf8 scripts\verify_phase_auth.py
D:\Anaconda3\envs\sqlbot\python.exe -X utf8 scripts\verify_phase2a_3a.py
```

结果：

| 脚本 | 结果 | 覆盖范围 |
|---|---:|---|
| `scripts/verify_phase_auth.py` | 42/42 通过 | Redis key、JSON 解析、过期判断、userType、tenant 解析、UserInfoDTO 构造、本地 admin 白名单 |
| `scripts/verify_phase2a_3a.py` | 24/24 通过 | SQL 表名解析、远程数据源 mock、AssistantOutDs TTL 缓存、权限 allow/deny/5xx/invalid_json/timeout、虚拟 Assistant 注入 |

已有自动报告：

- `H:\light\project\SQLBot-cx\docs\integration\变更\Phase-主线一-验证报告.md`
- `H:\light\project\SQLBot-cx\docs\integration\变更\Phase2a-3a-Mock验证报告.md`

## 5. 当前运行实例端到端验证

后端运行地址：`http://127.0.0.1:8011`，API 前缀：`/api/v1`。

### 5.1 无 token

请求：

```powershell
curl http://127.0.0.1:8011/api/v1/chat/list
```

结果：

```text
401
"Authentication invalid【Miss Token[X-SQLBOT-TOKEN or Authorization]!】"
```

结论：运行实例已要求 token。

### 5.2 伪造 token

请求：

```powershell
curl -H "Authorization: Bearer definitely-invalid" http://127.0.0.1:8011/api/v1/chat/list
```

结果：

```text
401
"Authentication invalid【Invalid or expired platform access token!】"
```

结论：strict 模式已生效，伪造 token 不会回退本地 JWT。

### 5.3 Redis mock token

向 `redis://127.0.0.1:6379/1` 写入短 TTL 测试 token：

```json
{
  "userId": 990001,
  "userType": 2,
  "tenantId": 124,
  "expiresTime": "未来时间",
  "userInfo": {
    "username": "codex_mock_user",
    "nickname": "Codex Mock User"
  }
}
```

请求结果：

| 场景 | Header | 结果 |
|---|---|---|
| Redis 命中、未过期、userType=2 | `Authorization: Bearer <mock>` | `200 {"code":0,"data":[],"msg":null}` |
| Redis 命中、未过期、userType=2 | `X-SQLBOT-TOKEN: Bearer <mock>` | `200 {"code":0,"data":[],"msg":null}` |
| userType=1 | `Authorization` | 401 |
| expired | `Authorization` | 401 |
| Redis miss | `Authorization` | 401 |

结论：真实运行实例已完成 `Redis token -> current_user -> chat/list` 端到端闭环。

## 6. 当前环境问题

### 6.1 登录代理地址不通或不匹配

当前配置：

```text
PLATFORM_AUTH_BASE_URL=http://127.0.0.1:8080
```

探测：

```text
curl http://127.0.0.1:8080/admin-api/system/tenant/get-id-by-name?name=八院
=> 连接失败
```

通过 SQLBot 登录代理发起加密假账号请求：

```text
POST /api/v1/login/access-token
=> 502 "Invalid platform auth login response."
```

判断：

- 登录代理代码存在，但当前运行环境未验证通过。
- 用户提供的数据中台后端入口是 `http://127.0.0.1:48080`，需要确认 auth API 是否也在 48080；如果是，应把 `PLATFORM_AUTH_BASE_URL` 改到 48080。

### 6.2 数据中台 OpenAPI 数据源接口未就绪

探测：

```text
GET http://127.0.0.1:48080/openapi/sqlbot/datasources/query
=> HTTP 200
=> {"code":404,"data":null,"msg":null,"traceId":null}
```

判断：

- SQLBot 侧 mock 契约已通过。
- 真实中台后端当前没有该接口，或路径/网关前缀不一致。
- 在该接口未就绪前，不能把 `PLATFORM_DATASOURCE_ENABLED=true` 当作已联调完成。

### 6.3 当前数据源与权限链路未在运行实例启用

当前配置：

```text
PLATFORM_DATASOURCE_ENABLED=false
PLATFORM_DATASOURCE_BASE_URL=http://127.0.0.1:48080
```

影响：

- chat/start 仍按原逻辑要求 datasource。
- TokenMiddleware 不注入虚拟 Assistant。
- 表权限 Gate 不会真正调用中台权限服务。

这是合理的保守配置，但说明“后端改造效果”目前只完成了认证主线的运行验证；数据源/权限主线仍处在 SQLBot 侧代码就绪、待中台接口联调阶段。

## 7. 风险

| 风险 | 影响 | 建议 |
|---|---|---|
| 认证依赖 Redis-only | Redis 抖动会导致普通用户全员 401 | 后续补中台 `check-token` 回退或 Redis 可用性监控 |
| `isAdmin` 推断粗糙 | 只按 `account == admin` 或 `user_id == 1` | 后续接中台 roles/scopes |
| `PLATFORM_AUTH_REQUIRE_USER_TYPE=2` | 中台管理员类账号会被拒绝 | 保留本地 admin 白名单，确认普通业务用户类型确实为 2 |
| 登录代理未连通 | 前端无法通过 SQLBot 登录入口换取中台 token | 修正 `PLATFORM_AUTH_BASE_URL` 并做真实账号登录验收 |
| 数据源接口缺失 | 远程数据源和单表限制无法端到端 | 先实现 `/openapi/sqlbot/datasources/query` |
| 权限接口缺失 | Gate 1 / Gate 2 只能 mock 通过 | 实现 `/openapi/sqlbot/table-permissions/check` 后再开 `PLATFORM_DATASOURCE_ENABLED=true` |

## 8. 建议下一步

1. 确认中台 auth API 真实地址。若在 `48080`，修改：

   ```text
   PLATFORM_AUTH_BASE_URL=http://127.0.0.1:48080
   ```

   然后用真实账号验证 `/api/v1/login/access-token` 返回 `access_token`。

2. 保持当前认证配置：

   ```text
   PLATFORM_AUTH_ENABLED=true
   PLATFORM_AUTH_STRICT_MODE=true
   PLATFORM_AUTH_ACCEPT_AUTHORIZATION_HEADER=true
   PLATFORM_AUTH_REDIS_URL=redis://127.0.0.1:6379/1
   PLATFORM_AUTH_REDIS_KEY_PREFIX=oauth2_access_token:%s
   PLATFORM_AUTH_REQUIRE_USER_TYPE=2
   ```

3. 中台后端补齐两个 P0 接口：

   ```text
   GET  /openapi/sqlbot/datasources/query
   POST /openapi/sqlbot/table-permissions/check
   ```

4. 两个接口可用后，再开启：

   ```text
   PLATFORM_DATASOURCE_ENABLED=true
   PLATFORM_DATASOURCE_BASE_URL=http://127.0.0.1:48080
   ```

5. 开启后执行真实联调：

   - 带中台 token 调 `POST /api/v1/chat/start`，验证 datasource 可不传。
   - 调 `POST /api/v1/chat/question`，验证数据源选择能从中台返回。
   - 故意问无权表，验证 SSE 返回 `permission_denied`。
   - 故意让权限服务 5xx/超时，验证 fail-closed，不执行 SQL。

## 9. 总体评价

本次改造的认证目标已经达到：SQLBot 后端可以直接接受数据中台 token，并从中台 Redis 恢复用户身份，当前本地运行实例也验证通过。

数据源和表权限目标属于下一阶段联调：SQLBot 侧框架已经做完，mock 也通过，但真实中台接口尚未就绪，并且当前配置明确关闭了远程数据源开关。因此不能称为“完整上线”，只能称为“SQLBot 侧代码完成，等待中台接口与环境配置完成端到端验收”。
