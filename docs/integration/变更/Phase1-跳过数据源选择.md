# Phase 1 变更说明（2026-05-06）

> 本文是数据源改造（主线二）**Phase 1**「跳过数据源选择」的全部代码改动详细说明，可直接用于团队会议或文档宣讲。
>
> 配套阅读：[`数据源改造方案.md`](./数据源改造方案.md)（完整方案）、[`配置手册-环境变量.md`](./配置手册-环境变量.md)（配置字段）。

## 一、目标回顾

让 SQLBot 在统一中台视角下**不再要求用户在 chat 创建阶段绑定数据源**。改完之后：
- 默认行为完全不变（向后兼容）
- 打开开关后，前端可以传 `datasource=null` 创建 chat
- chat/question 时再让 LLM 自动选择数据源（这部分由 Phase 2 接管）

## 二、改动范围

**仅 2 个后端文件，3 处改动**。**0 个 DB 迁移、0 个前端改动**。

| 文件 | 改动 | 行数 |
|---|---|---|
| `backend/common/core/config.py` | 新增 `PLATFORM_DATASOURCE_*` / `PLATFORM_PERMISSION_*` 共 8 个配置字段 | +18 |
| `backend/common/core/config.py` | `lowercase_bool` validator 注册新增的 `PLATFORM_DATASOURCE_ENABLED` 布尔字段 | +1 |
| `backend/apps/chat/api/chat.py` | `start_chat` 根据 `PLATFORM_DATASOURCE_ENABLED` 决定 `require_datasource` 参数 | +3 |

## 三、详细变更（按文件）

### 3.1 `backend/common/core/config.py`（新增配置字段）

**变更前**：`Settings` 类下只有 `PLATFORM_AUTH_*` 一族配置。

**变更后**：在 `PLATFORM_AUTH_LOCAL_ADMIN_ACCOUNTS` 之后追加：

```python
# === 数据中台 data 服务（数据源元数据来源，主线二 Phase 2 启用）===
# 开关：为 false 时 SQLBot 行为完全与当前一致（本地 ds），为 true 时 TokenMiddleware
# 会在解析出中台身份后注入虚拟 assistant 并由 AssistantOutDs 远程拉取 ds 列表
PLATFORM_DATASOURCE_ENABLED: bool = False
PLATFORM_DATASOURCE_BASE_URL: str | None = None
PLATFORM_DATASOURCE_LIST_PATH: str = "/openapi/sqlbot/datasources/query"
PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS: float = 10.0
# AssistantOutDs 远程拉取结果的本地 TTL 缓存秒数，避免打爆中台
PLATFORM_DATASOURCE_CACHE_TTL_SECONDS: int = 60

# === 表级权限校验（主线二 Phase 3 启用，fail-closed）===
PLATFORM_PERMISSION_CHECK_PATH: str = "/openapi/sqlbot/table-permissions/check"
PLATFORM_PERMISSION_APPLY_PATH: str = "/openapi/sqlbot/permission-applies"
# 未匹配到 ds 时记录问题（供数据治理侧消费）
PLATFORM_UNMATCHED_QUESTION_PATH: str = "/openapi/sqlbot/unmatched-questions"
```

并在文件末尾的 `lowercase_bool` field_validator 注册列表中追加：

```python
'PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN',
'PLATFORM_DATASOURCE_ENABLED',  # 新增
mode='before')
```

**为什么这么做**：
1. **命名遵循 `PLATFORM_*` 前缀风格**，与主线一已有的 `PLATFORM_AUTH_*` 一致，让人一眼能看出是中台集成相关。
2. **默认值全部"行为不变"**：`ENABLED=False`、URL 为空——即便不动 `.env`，旧部署的行为完全等同于改造前。
3. **三族配置一次性占位**（datasource / permission / unmatched-question），避免 Phase 2/3 时再回头改 config 文件。
4. **`PLATFORM_DATASOURCE_ENABLED` 必须注册到 `lowercase_bool`**：pydantic-settings 默认对 bool 字段不识别 `.env` 里的字符串 "true/false"，要走 SQLBot 现有的统一字符串→bool 转换器。

### 3.2 `backend/apps/chat/api/chat.py`（放开 chat/start 必填）

**变更点 1：导入 `settings`**

```python
# 变更前
from common.core.deps import CurrentAssistant, SessionDep, CurrentUser, Trans

# 变更后
from common.core.config import settings  # 新增
from common.core.deps import CurrentAssistant, SessionDep, CurrentUser, Trans
```

**变更点 2：`start_chat` 函数体**

```python
# 变更前
async def start_chat(session: SessionDep, current_user: CurrentUser, create_chat_obj: CreateChat):
    try:
        return create_chat(session, current_user, create_chat_obj)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 变更后
async def start_chat(session: SessionDep, current_user: CurrentUser, create_chat_obj: CreateChat):
    try:
        # 主线二 Phase 1：当中台数据源接入开启时，允许 chat/start 不带 datasource，
        # 由 chat/question 阶段从中台远程拉取候选并自动选择。开关关闭时保持原逻辑（必填）。
        require_datasource = not settings.PLATFORM_DATASOURCE_ENABLED
        return create_chat(session, current_user, create_chat_obj, require_datasource=require_datasource)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**为什么这么做**：

1. **改动收敛在 1 个 API 端点的 1 行参数**，不动 `create_chat` 本身：
   - `create_chat(..., require_datasource=...)` 这个参数早就有，原本只在 `assistant/start` 路径用过 `False` 值，我们只是把它接入到 `chat/start`
   - 见 `apps/chat/curd/chat.py:699` 函数签名，本来就支持 `require_datasource: bool = True`

2. **配置开关驱动，向后兼容**：
   - `PLATFORM_DATASOURCE_ENABLED=false`（默认）→ `require_datasource=True` → 行为等同改造前
   - `PLATFORM_DATASOURCE_ENABLED=true` → `require_datasource=False` → 允许无 ds 创建

3. **不需要改 `@require_permissions` 装饰器**：
   - 装饰器最终调用 `check_ws_permission(oid, type, resource)`（`apps/system/schemas/permission.py:37`）
   - 该函数第 38 行：`if not resource or (isinstance(resource, list) and len(resource) == 0): return True`
   - 即 `datasource=None` 时装饰器自动放行，不需要任何改动
   - **这是阅读现有代码时发现的免费便利，比预期省了一处改动**

4. **不需要改 `CreateChat` schema**：
   - `CreateChat.datasource: int = None`（`chat_model.py:168`）本来就声明为可选
   - pydantic 默认接受 `None` 输入，无需 `Optional` 标注变更

5. **不需要改 `create_chat` 函数体**：
   - `apps/chat/curd/chat.py:699-771` 在 `datasource=None + require_datasource=False` 路径下天然安全：
     - L713 `if create_chat_obj.datasource:` 跳过 ds 加载
     - L729 `else: chat.engine_type = ''` 设空字符串
     - L744 `if require_datasource and ds:` 跳过首记录创建
     - 全程不会访问 `ds.name` / `ds.id` 等空属性

## 四、向后兼容性证明

| 场景 | 改造前行为 | 改造后行为 | 是否一致 |
|---|---|---|---|
| 默认部署（不动 `.env`） | chat/start 必填 datasource | `ENABLED=false` 默认值 → `require_datasource=True` → 必填 | ✅ |
| 老前端传 `datasource=123` | 创建 chat 并绑定 ds | 同上（开关无关） | ✅ |
| 老前端传 `datasource=null` | 抛 `Datasource cannot be None` | 开关关时抛同样异常；开关开时创建无 ds chat | ✅ 关时一致；开时按设计 |
| `assistant/start` 路径 | 用 `create_chat_obj.datasource` 作 ds | 行为完全没动 | ✅ |
| chat/question 路径 | 从 `chat.datasource` 读 ds | 读到 None 时按现有 `select_datasource` 走（Phase 2 接管） | ✅ |

## 五、验收测试

**Phase 1 验收清单**（部署时跑一遍即可）：

| # | 测试 | 预期 |
|---|---|---|
| T1 | 不设 `PLATFORM_DATASOURCE_ENABLED`，前端传 `datasource=123` 调 `/chat/start` | 200，行为与改造前一致 |
| T2 | 不设 `PLATFORM_DATASOURCE_ENABLED`，前端不传 `datasource` 调 `/chat/start` | 500 `Datasource cannot be None`（与改造前一致） |
| T3 | 设 `PLATFORM_DATASOURCE_ENABLED=true`，前端不传 `datasource` 调 `/chat/start` | 200，返回 `chat_id`，无首记录 |
| T4 | 设 `PLATFORM_DATASOURCE_ENABLED=true`，前端传 `datasource=123` 调 `/chat/start` | 200，绑定 ds 123（行为与改造前一致） |
| T5 | T3 创建的 chat，调 `/chat/question` | 触发 `select_datasource`（仍走本地 CoreDatasource，Phase 2 接管远程） |

**回滚方法**：
- 配置层回滚：删除/注释 `.env` 里的 `PLATFORM_DATASOURCE_ENABLED`
- 代码层回滚：`git revert` 这两个文件即可，无 DB 迁移

## 六、给团队成员的速查

**面向 Reviewer**：改动 3 处，全部可在一屏内 review 完，无 DB 变更，无前端变更。

**面向 QA**：默认部署回归无影响（T1/T2/T4），新功能验证只看 T3/T5。

**面向 DevOps**：上线只需保持 `.env` 不变即可零影响；当中台 data 服务 ready，再加 `PLATFORM_DATASOURCE_ENABLED=true` + `PLATFORM_DATASOURCE_BASE_URL=...` 进入 Phase 2。

**面向中台后端**：本次改动**不涉及中台**，纯 SQLBot 内部解锁。中台需要在 Phase 2 提供：
- `POST /openapi/sqlbot/datasources/query` （数据源列表）
- `POST /openapi/sqlbot/table-permissions/check`（Phase 3，权限校验）

**面向前端**：本次改动**不需要前端配合**。Phase 2/3 起前端要适配 SSE 新事件（`datasource_not_found` / `permission_denied`）。

## 七、未做但记录在案

以下属于后续 Phase，**本次未涉及**：

| 项 | Phase | 说明 |
|---|---|---|
| `TokenMiddleware` 注入虚拟 assistant | Phase 2 | 让 chat/question 自动走中台远程 ds |
| `AssistantOutDs` 加 LRU/TTL 缓存 | Phase 2 | 当前 `__init__` 每次同步拉 HTTP，并发场景会爆 |
| 数据源匹配 7 步策略 + LLM 重排 | Phase 2/3 | 详见 `数据源改造方案.md` §6 |
| `Chat.table_list` 字段 + alembic migration | Phase 3 | 表级绑定 |
| 双 Gate 权限校验（generate_sql 后 + execute_sql 前） | Phase 3 | 详见 `数据源改造方案.md` §7 |
| SSE 事件协议（`datasource_not_found` 等） | Phase 3 | 前端需配合改造 |
| refresh / logout 代理 | 主线一收尾 | 与本次主线二无关，但 token 改造的遗留项 |
