"""
Phase 2a / 3a 契约级自验。

每条用例按「传入 / 期望 / 实际」三栏记录，跑完后：
- stdout 打 PASS/FAIL
- 自动生成 markdown 报告到 docs/integration/变更/Phase2a-3a-Mock验证报告.md
- 报告末尾附"中台需开发内容"小节，便于跨团队对齐

不起 uvicorn、不连业务 DB、不调 LLM。仅针对我们改造的几个关键代码点跑用例：

1. sql_parser.extract_table_names + merge_tables
2. AssistantOutDs.get_ds_from_api（mock GET /datasources/query）
3. AssistantOutDsFactory（TTL 缓存 + token 切换）
4. platform_permission.check_table_permissions（allow / deny / 5xx / 超时 / 无效 JSON / 空表名）
5. denied_tables_to_sse_payload（SSE payload 形状）
6. _inject_platform_assistant_if_enabled（虚拟 assistant 形状）

依赖：
- 终端 A: python scripts/mock_platform.py        (监听 127.0.0.1:9999)
- 运行:   python scripts/verify_phase2a_3a.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 必须按 main.py 顺序：xpack 先 import，触发它内部 import 完业务模块；
# 直接 import apps.system.crud.assistant 会撞循环依赖。
import sqlbot_xpack  # noqa: E402,F401
import apps.api  # noqa: E402,F401

MOCK_BASE = os.environ.get("MOCK_BASE", "http://127.0.0.1:9999")
REPORT_OUT = os.environ.get(
    "REPORT_OUT",
    os.path.abspath(
        os.path.join(_BACKEND, os.pardir, "docs", "integration", "变更", "Phase2a-3a-Mock验证报告.md")
    ),
)


records: list[dict[str, Any]] = []


def _short(v: Any, lim: int = 240) -> str:
    s = repr(v) if not isinstance(v, str) else v
    return (s[: lim - 3] + "...") if len(s) > lim else s


def case(name: str, given: Any, expected: Any, actual: Any, ok: bool, group: str = "") -> None:
    records.append({
        "group": group,
        "name": name,
        "given": _short(given),
        "expected": _short(expected),
        "actual": _short(actual),
        "ok": ok,
    })
    tag = "[PASS]" if ok else "[FAIL]"
    if ok:
        print(f"{tag} {name}")
    else:
        print(f"{tag} {name}\n        given={_short(given)}\n     expected={_short(expected)}\n       actual={_short(actual)}")


def _ensure_mock_running():
    import urllib.request
    try:
        urllib.request.urlopen(f"{MOCK_BASE}/openapi/sqlbot/datasources/query", timeout=2).read()
    except Exception as e:
        print(f"!! mock 不可达 ({MOCK_BASE}) : {e}")
        print("   请先启动: python scripts/mock_platform.py")
        sys.exit(1)


# =====================================================================
# Group 1: sql_parser
# =====================================================================
def test_sql_parser():
    from common.utils.sql_parser import extract_table_names, merge_tables
    g = "1. sql_parser（双 Gate 表名解析）"

    cases = [
        ("simple", "SELECT * FROM orders", {"orders"}),
        ("join", "SELECT * FROM orders o JOIN users u ON o.user_id=u.id", {"orders", "users"}),
        ("cte_excludes_alias", "WITH t AS (SELECT * FROM orders) SELECT * FROM t", {"orders"}),
        ("subquery", "SELECT * FROM (SELECT * FROM payments) p WHERE p.amount > 0", {"payments"}),
        ("union", "SELECT id FROM a UNION SELECT id FROM b", {"a", "b"}),
        ("empty_string", "", set()),
    ]
    for name, sql, expected in cases:
        actual = extract_table_names(sql)
        case(f"sql_parser/{name}", sql, expected, actual, actual == expected, g)

    bad_sql = "THIS IS NOT VALID SQL ;;; ;; FROM"
    actual = extract_table_names(bad_sql)
    case("sql_parser/parse_fail_returns_none",
         bad_sql, "None（fail-open，让上层 fail-closed）", actual, actual is None, g)

    actual = merge_tables({"orders"}, ["users", "orders"], None, [])
    case("sql_parser/merge_dedupe",
         "({'orders'}, ['users','orders'], None, [])", {"orders", "users"},
         actual, actual == {"orders", "users"}, g)


# =====================================================================
# Group 2: AssistantOutDs + Factory
# =====================================================================
def _build_virtual_assistant(token: str = "Bearer testtoken-1", oid: int = 124, endpoint_suffix: str = ""):
    from apps.system.schemas.system_schema import AssistantHeader
    return AssistantHeader(
        id=0,
        name="platform-virtual",
        domain=MOCK_BASE,
        type=1,
        configuration=json.dumps({
            "endpoint": f"/openapi/sqlbot/datasources/query{endpoint_suffix}",
            "timeout": 5,
            "oid": oid,
            "platform_virtual": True,
        }),
        certificate=json.dumps([
            {"target": "header", "key": "Authorization", "value": token},
            {"target": "header", "key": "tenant-id", "value": str(oid)},
        ]),
        oid=oid,
        online=True,
    )


def test_out_ds_factory():
    from apps.system.crud.assistant import AssistantOutDs, AssistantOutDsFactory
    g = "2. AssistantOutDs + AssistantOutDsFactory（远程拉取 + TTL 缓存）"

    a1 = _build_virtual_assistant("Bearer aaa", 124)
    inst1 = AssistantOutDsFactory.get_instance(a1)
    inst1b = AssistantOutDsFactory.get_instance(a1)
    case("factory/cache_hit_same_token",
         "同 (token, oid, configuration) 连续调用 2 次",
         "返回同一 instance（is 同对象）",
         f"is_same={inst1 is inst1b}",
         inst1 is inst1b, g)

    case("factory/ds_list_loaded",
         "mock 返回 1 个固定 ds",
         "ds_list 长度 ≥ 1",
         f"len={len(inst1.ds_list) if inst1.ds_list else 0}",
         isinstance(inst1.ds_list, list) and len(inst1.ds_list) >= 1, g)

    a2 = _build_virtual_assistant("Bearer bbb", 124)
    inst2 = AssistantOutDsFactory.get_instance(a2)
    case("factory/diff_token_diff_instance",
         "token 从 'Bearer aaa' 换成 'Bearer bbb'（其余不变）",
         "新 instance（防 token 串号）",
         f"is_same={inst2 is inst1}",
         inst2 is not inst1, g)

    raised: Exception | None = None
    try:
        AssistantOutDs(_build_virtual_assistant("Bearer ccc-fail", 124, endpoint_suffix="?fail=1"))
    except Exception as e:
        raised = e
    case("out_ds/fail_500_raises",
         "中台 ds 接口返回 500（mock ?fail=1）",
         "AssistantOutDs.__init__ 抛异常（让 chat 主流程 catch 退化）",
         f"raised={type(raised).__name__ if raised else 'None'}",
         raised is not None, g)


# =====================================================================
# Group 3: platform_permission
# =====================================================================
def _force_settings_for_perm(enabled: bool = True, mode: str = "allow"):
    from common.core.config import settings
    settings.PLATFORM_DATASOURCE_ENABLED = enabled
    settings.PLATFORM_DATASOURCE_BASE_URL = MOCK_BASE
    settings.PLATFORM_PERMISSION_CHECK_PATH = f"/openapi/sqlbot/table-permissions/check?mode={mode}"
    settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS = 5.0
    settings.PLATFORM_AUTH_TENANT_ID_HEADER = "tenant-id"


def test_permission_client():
    from common.utils.platform_permission import (
        check_table_permissions,
        denied_tables_to_sse_payload,
        is_permission_check_enabled,
    )
    g = "3. platform_permission 客户端（fail-closed 策略）"

    _force_settings_for_perm(enabled=False)
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds_mock_sales",
        sql="SELECT * FROM orders", tables=["orders"],
    )
    case("perm/disabled_allows",
         "PLATFORM_DATASOURCE_ENABLED=false",
         "allowed=True（不校验放行，保持改造前行为）",
         f"allowed={r.allowed}", r.allowed, g)

    _force_settings_for_perm(enabled=True, mode="allow")
    case("perm/enabled_helper",
         "ENABLED=true 且配了 BASE_URL",
         "is_permission_check_enabled() = True",
         is_permission_check_enabled(), is_permission_check_enabled(), g)

    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds_mock_sales",
        sql="SELECT * FROM users", tables=["users"],
    )
    case("perm/allow_path",
         "tables=['users'], mock mode=allow",
         "allowed=True, deniedTables=[]",
         f"allowed={r.allowed}, denied={[t.name for t in r.denied_tables]}",
         r.allowed and not r.denied_tables, g)

    _force_settings_for_perm(enabled=True, mode="deny")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds_mock_sales",
        sql="SELECT * FROM orders", tables=["orders", "users"],
    )
    case("perm/deny_returns_denied_tables",
         "tables=['orders','users'], mock mode=deny（默认拒 orders/payments）",
         "allowed=False, deniedTables 含 'orders'",
         f"allowed={r.allowed}, denied={[t.name for t in r.denied_tables]}",
         (not r.allowed) and any(t.name == "orders" for t in r.denied_tables), g)

    sse = denied_tables_to_sse_payload(r, gate_label="gate1")
    sse_ok = (
        sse.get("type") == "permission_denied"
        and sse.get("gate") == "gate1"
        and isinstance(sse.get("deniedTables"), list)
        and any(t.get("applyUrl", "").startswith("http") for t in sse["deniedTables"])
        and any(a.get("type") == "permission_apply" for a in sse.get("actions", []))
    )
    case("perm/sse_payload_shape",
         "上一个 deny 结果, gate_label='gate1'",
         "type='permission_denied', gate='gate1', deniedTables 含 applyUrl, actions 含 'permission_apply'",
         f"type={sse.get('type')}, gate={sse.get('gate')}, denied_count={len(sse.get('deniedTables', []))}",
         sse_ok, g)

    _force_settings_for_perm(enabled=True, mode="error500")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="SELECT * FROM orders", tables=["orders"],
    )
    case("perm/fail_closed_on_5xx",
         "mock mode=error500（HTTP 500）",
         "allowed=False, reason 以 'permission_service_status_' 开头",
         f"allowed={r.allowed}, reason={r.reason}",
         (not r.allowed) and (r.reason or "").startswith("permission_service_status_"), g)

    _force_settings_for_perm(enabled=True, mode="invalid_json")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="SELECT * FROM orders", tables=["orders"],
    )
    case("perm/fail_closed_on_invalid_json",
         "mock 返 200 但 body 不是合法 JSON",
         "allowed=False, reason='permission_service_invalid_json'",
         f"allowed={r.allowed}, reason={r.reason}",
         (not r.allowed) and r.reason == "permission_service_invalid_json", g)

    _force_settings_for_perm(enabled=True, mode="timeout")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="SELECT * FROM orders", tables=["orders"],
        timeout=1.0,
    )
    case("perm/fail_closed_on_timeout",
         "mock 睡眠 60s, 调用方 timeout=1s",
         "allowed=False, reason 以 'permission_service_unreachable' 开头",
         f"allowed={r.allowed}, reason={r.reason}",
         (not r.allowed) and (r.reason or "").startswith("permission_service_unreachable"), g)

    _force_settings_for_perm(enabled=True, mode="allow")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="", tables=[],
    )
    case("perm/empty_tables_allows",
         "tables=[]（解析不到表）",
         "allowed=True（无可校验对象，直接放行）",
         f"allowed={r.allowed}", r.allowed, g)


# =====================================================================
# Group 4: 虚拟 Assistant 注入
# =====================================================================
def test_virtual_assistant_shape():
    from apps.system.middleware import auth as auth_mod
    from common.core.config import settings
    g = "4. 虚拟 Assistant 注入（_inject_platform_assistant_if_enabled）"

    settings.PLATFORM_DATASOURCE_ENABLED = True
    settings.PLATFORM_DATASOURCE_BASE_URL = MOCK_BASE
    settings.PLATFORM_DATASOURCE_LIST_PATH = "/openapi/sqlbot/datasources/query"
    settings.PLATFORM_AUTH_TENANT_ID_HEADER = "tenant-id"

    class _State:
        assistant = None
        current_user = type("U", (), {"oid": 124})()

    class _Req:
        state = _State()

    inst = auth_mod.TokenMiddleware(app=None)
    inst._inject_platform_assistant_if_enabled(_Req(), token_value="Bearer fake-token")
    a = _Req.state.assistant
    if a is None:
        case("inject/builds_assistant", "PLATFORM_DATASOURCE_ENABLED=true, current_user.oid=124, token=Bearer fake-token", "构造 AssistantHeader 注入 request.state.assistant", "None（未构造）", False, g)
        return

    cfg = json.loads(a.configuration)
    cert = json.loads(a.certificate)
    keys_in_cert = {c.get("key") for c in cert}

    case("inject/type_is_1",
         "ENABLED=true + 有 oid + 有 token",
         "AssistantHeader.type=1（落入 dynamic_ds_types，触发 AssistantOutDs 链路）",
         f"type={a.type}",
         a.type == 1, g)

    case("inject/configuration_keys",
         "configuration JSON 应含必要字段",
         "{endpoint, timeout, oid, platform_virtual}",
         f"keys={set(cfg.keys())}",
         {"endpoint", "timeout", "oid", "platform_virtual"}.issubset(set(cfg.keys())), g)

    case("inject/certificate_has_auth_and_tenant",
         "certificate 应含两个透传 header",
         "{Authorization, tenant-id}",
         f"keys={keys_in_cert}",
         "Authorization" in keys_in_cert and "tenant-id" in keys_in_cert, g)


# =====================================================================
# 输出 markdown 报告
# =====================================================================
PLATFORM_API_REQUIREMENTS = """## 五、需要数据中台后端开发的接口

> 本节给中台后端同事 review 用。所有契约约定都已在本次 mock 验证中实测通过；
> 真接口实现后，把 SQLBot 配置 `PLATFORM_DATASOURCE_BASE_URL` 指向真实地址即可联调。

### 5.1 数据源查询（必做，Phase 2b）

```
GET  /openapi/sqlbot/datasources/query
Headers:
  Authorization: Bearer <SQLBot 透传的中台 OAuth2 access_token>
  tenant-id:     <SQLBot 透传的 tenantId / oid，integer>
Response 200:
  {
    "code": 0,
    "data": [
      {
        "id":        <int>,                  // ⚠️ 当前 SQLBot AssistantOutDsSchema.id 是 Optional[int]
        "name":      <str>,                  // 数据源显示名
        "type":      "mysql"|"pg"|"oracle"|...,
        "type_name": <str>,                  // 可选
        "description": <str>,                // 可选
        "host":      <str>,
        "port":      <int>,
        "dataBase":  <str>,
        "user":      <str>,
        "password":  <str>,                  // 该字段 SQLBot 不会落库，只在内存中使用
        "db_schema": <str>,                  // 可选（PG/Oracle 用）
        "tables": [
          {
            "name": "orders",
            "comment": "订单主表",
            "fields": [
              { "name": "id",     "type": "bigint",  "comment": "主键" },
              { "name": "amount", "type": "numeric", "comment": "金额" }
            ]
          }
        ]
      }
    ]
  }
Response 5xx / code != 0:
  - SQLBot 会抛异常并对前端 SSE 推 datasource_not_found
  - **fail-closed**：中台不可达时不会放行查询
```

**字段对齐落到 SQLBot `apps/system/schemas/system_schema.py:189 AssistantOutDsSchema`。**

### 5.2 表级权限校验（必做，Phase 3b）

```
POST /openapi/sqlbot/table-permissions/check
Headers:
  Authorization: Bearer <token>
  tenant-id:     <oid>
  Content-Type:  application/json
Body:
  {
    "datasourceId": <int|str>,           // 中台 ds id
    "sql":          "SELECT ...",        // SQLBot 要执行的最终 SQL（Gate 2 时是改写后的 SQL）
    "tables":       [{"name": "orders"}, {"name": "users"}],
    "chatId":       <int>,               // 可选，审计上下文
    "recordId":     <int>                // 可选，审计上下文
  }
Response 200 - 全部通过:
  {
    "code": 0,
    "data": {
      "allowed": true,
      "deniedTables": [],
      "datasourceId": <id>
    }
  }
Response 200 - 有拒绝:
  {
    "code": 0,
    "data": {
      "allowed": false,
      "deniedTables": [
        {
          "name":        "orders",
          "displayName": "订单表",
          "catalogPath": "sales/orders",
          "reason":      "no permission",
          "applyUrl":    "http://platform.local/apply?table=orders"
        }
      ],
      "datasourceId": <id>
    }
  }
```

**SQLBot 双 Gate 调用时机：**
- Gate 1：`generate_sql` 后立即调用（防 LLM 选了无权表）
- Gate 2：`execute_sql` 前再调一次（防 generate_filter / 动态 SQL 改写引入新表）

任一返回 `allowed=false` → SQLBot 通过 SSE 推 `permission_denied` 事件 + applyUrl，前端引导用户申请权限。

### 5.3 权限申请（可选，Phase 3c）

```
POST /openapi/sqlbot/permission-applies
Body: { "tables": [...], "chatId": ..., "reason": "..." }
```

让用户点 SSE 中 actions[0] 时直接提交申请。如果中台已有内部 portal，可以仅提供 `applyUrl` 让前端跳转，不必新开接口。

### 5.4 未匹配问题记录（可选，Phase 3c）

```
POST /openapi/sqlbot/unmatched-questions
Body: { "question": "...", "candidateDatasources": [...], "chatId": ... }
```

当 SQLBot 选不到合适数据源时，让数据治理团队收到信号，便于补数据建模。

### 5.5 协议层待决问题（评审会要拉齐）

| # | 问题 | 当前 SQLBot 侧约束 | 期望中台明确 |
|---|---|---|---|
| 1 | datasource id 类型 | `Optional[int]` | int 还是 string？若是 string，SQLBot schema 需放宽 |
| 2 | `code` 成功值 | 接受 `code in (0, 200)` | 中台实际用哪个 |
| 3 | 鉴权头命名 | `Authorization` + `tenant-id` | 是否完全一致；tenant-id 是否区分大小写 |
| 4 | datasources/query HTTP 方法 | GET（沿用 `AssistantOutDs.get_ds_from_api`）| 中台是否一致；如用 POST 需通知 |
| 5 | 表名匹配大小写规则 | `extract_table_names` 不归一大小写 | 中台权限存储的 table name 大小写规则 |
"""


def write_markdown_report():
    total = len(records)
    failed = sum(1 for r in records if not r["ok"])
    passed = total - failed

    lines: list[str] = []
    lines.append("# Phase 2a / 3a Mock 验证报告")
    lines.append("")
    lines.append(f"> 自动生成自 `backend/scripts/verify_phase2a_3a.py`，{datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")
    lines.append("## 一、总览")
    lines.append("")
    lines.append(f"- 用例总数：**{total}**")
    lines.append(f"- 通过：**{passed}**")
    lines.append(f"- 失败：**{failed}**")
    lines.append(f"- 结论：**{'全部通过 ✅' if failed == 0 else '存在失败用例 ❌，详见下表'}**")
    lines.append("")
    lines.append("## 二、用例明细（每条都列：传入 / 期望 / 实际）")
    lines.append("")

    cur_group = None
    for idx, r in enumerate(records):
        if r["group"] != cur_group:
            cur_group = r["group"]
            lines.append(f"### {cur_group}")
            lines.append("")
            lines.append("| 用例 | 传入 | 期望 | 实际 | 结果 |")
            lines.append("|---|---|---|---|---|")
        lines.append(
            "| `{name}` | {given} | {expected} | {actual} | {tag} |".format(
                name=r["name"],
                given=str(r["given"]).replace("|", "\\|").replace("\n", " "),
                expected=str(r["expected"]).replace("|", "\\|").replace("\n", " "),
                actual=str(r["actual"]).replace("|", "\\|").replace("\n", " "),
                tag="✅" if r["ok"] else "❌",
            )
        )
        is_last = idx == len(records) - 1
        if is_last or records[idx + 1]["group"] != cur_group:
            lines.append("")

    lines.append("## 三、复现步骤")
    lines.append("")
    lines.append("```powershell")
    lines.append("conda activate sqlbot")
    lines.append("cd H:\\light\\project\\SQLBot-cx\\backend")
    lines.append("# 终端 A:")
    lines.append("python scripts/mock_platform.py")
    lines.append("# 终端 B:")
    lines.append("python scripts/verify_phase2a_3a.py")
    lines.append("```")
    lines.append("")

    lines.append("## 四、未覆盖的部分（联调阶段补）")
    lines.append("")
    lines.append("| 范围 | 为什么本次没测 |")
    lines.append("|---|---|")
    lines.append("| chat 主流程端到端（提问 → SSE） | 需要真实 LLM key + 业务 DB；本次只验契约层 |")
    lines.append("| Gate 1 / Gate 2 在 `llm.py:1187 / 1247` 实际触发 | 需要 chat/question 真跑；本次仅验 `_do_permission_check` 直接调用 |")
    lines.append("| `chat/start` 不传 datasource 时是否真放行 | 需起 uvicorn 跑 HTTP，下一步联调时 curl 即可 |")
    lines.append("| 前端解析 SSE payload 是否正确渲染 | 跨工程，由前端同事按 §5 协议实现 |")
    lines.append("")

    lines.append(PLATFORM_API_REQUIREMENTS)

    os.makedirs(os.path.dirname(REPORT_OUT), exist_ok=True)
    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入: {REPORT_OUT}")


def main():
    _ensure_mock_running()
    print(f"== verify Phase 2a/3a against mock at {MOCK_BASE} ==")
    for fn in (test_sql_parser, test_out_ds_factory, test_permission_client, test_virtual_assistant_shape):
        try:
            fn()
        except Exception as e:
            traceback.print_exc()
            case(fn.__name__, "(运行整个分组)", "正常完成", f"exception {type(e).__name__}: {e}", False, fn.__name__)

    total = len(records)
    failed = sum(1 for r in records if not r["ok"])
    print()
    print(f"== {total - failed}/{total} passed, {failed} failed ==")

    write_markdown_report()

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
