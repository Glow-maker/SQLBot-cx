"""
Phase 2a / 3a 契约级自验。

不起 uvicorn、不连业务 DB、不调 LLM。仅针对我们改造的几个关键代码点跑用例：

1. sql_parser.extract_table_names      —— 解析正确性 + fail-open 行为
2. sql_parser.merge_tables             —— LLM 表 + parser 表合并去重
3. AssistantOutDs.get_ds_from_api      —— 拉取中台 ds（mock GET /datasources/query）
4. AssistantOutDsFactory                —— TTL 缓存 + token 切换不串号
5. platform_permission.check_table_permissions —— allow / deny / 5xx / 超时 / 无效 JSON
6. denied_tables_to_sse_payload        —— SSE payload 形状

依赖：
- 提前在另一个 shell 跑 mock：
    python scripts/mock_platform.py
- 默认 mock 在 127.0.0.1:9999

运行：
    python scripts/verify_phase2a_3a.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

# 让 import 链能走到 backend/{apps,common}
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# 验证不需要数据库 / 业务表，强制最小依赖
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 必须按 main.py 的顺序：xpack 先 import，并触发它内部 import 完业务模块；
# 直接 import apps.system.crud.assistant 会撞循环依赖。
import sqlbot_xpack  # noqa: E402,F401
import apps.api  # noqa: E402,F401  让 SQLBot 业务模块树先初始化

MOCK_BASE = os.environ.get("MOCK_BASE", "http://127.0.0.1:9999")
PASS = "[PASS]"
FAIL = "[FAIL]"

results: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    tag = PASS if ok else FAIL
    print(f"{tag} {name}" + (f"  -- {detail}" if detail else ""))


def _ensure_mock_running() -> None:
    import urllib.request
    try:
        urllib.request.urlopen(f"{MOCK_BASE}/openapi/sqlbot/datasources/query", timeout=2).read()
    except Exception as e:
        print(f"!! mock 不可达 ({MOCK_BASE}) : {e}")
        print("   请先在另一个 shell 跑：python scripts/mock_platform.py")
        sys.exit(1)


# ---- 1. sql_parser.extract_table_names ----
def test_sql_parser():
    from common.utils.sql_parser import extract_table_names, merge_tables

    cases = [
        ("simple",
         "SELECT * FROM orders",
         {"orders"}),
        ("join",
         "SELECT * FROM orders o JOIN users u ON o.user_id=u.id",
         {"orders", "users"}),
        ("cte_excludes_alias",
         "WITH t AS (SELECT * FROM orders) SELECT * FROM t",
         {"orders"}),
        ("subquery",
         "SELECT * FROM (SELECT * FROM payments) p WHERE p.amount > 0",
         {"payments"}),
        ("union",
         "SELECT id FROM a UNION SELECT id FROM b",
         {"a", "b"}),
        ("empty",
         "",
         set()),
    ]
    for name, sql, expected in cases:
        got = extract_table_names(sql)
        ok = got == expected
        _record(f"sql_parser/{name}", ok, f"got={got} expected={expected}" if not ok else "")

    # fail-open: 解析失败返回 None（不抛）
    bad = extract_table_names("THIS IS NOT VALID SQL ;;; ;; FROM")
    _record("sql_parser/parse_fail_returns_none", bad is None, f"got={bad}")

    merged = merge_tables({"orders"}, ["users", "orders"], None, [])
    _record("sql_parser/merge_dedupe", merged == {"orders", "users"}, f"got={merged}")


# ---- 2. AssistantOutDs.get_ds_from_api（走 mock） ----
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

    a1 = _build_virtual_assistant("Bearer aaa", 124)
    inst1 = AssistantOutDsFactory.get_instance(a1)
    inst1b = AssistantOutDsFactory.get_instance(a1)
    _record("factory/cache_hit_same_token", inst1 is inst1b)
    _record("factory/ds_list_loaded", isinstance(inst1.ds_list, list) and len(inst1.ds_list) >= 1,
            f"len={len(inst1.ds_list) if inst1.ds_list else 0}")

    a2 = _build_virtual_assistant("Bearer bbb", 124)
    inst2 = AssistantOutDsFactory.get_instance(a2)
    _record("factory/diff_token_diff_instance", inst2 is not inst1)

    # 中台 5xx → 抛异常（chat 主流程会 catch 并退化）
    try:
        AssistantOutDs(_build_virtual_assistant("Bearer ccc-fail", 124, endpoint_suffix="?fail=1"))
        _record("out_ds/fail_500_raises", False, "应当抛异常但没抛")
    except Exception as e:
        _record("out_ds/fail_500_raises", True, f"raised: {type(e).__name__}")


# ---- 3. platform_permission.check_table_permissions ----
def _force_settings_for_perm(enabled: bool = True, mode: str = "allow"):
    from common.core.config import settings
    settings.PLATFORM_DATASOURCE_ENABLED = enabled
    settings.PLATFORM_DATASOURCE_BASE_URL = MOCK_BASE
    # 把 mode 拼到 path 上，让 mock 走对应分支（mock 支持 query string 覆盖）
    settings.PLATFORM_PERMISSION_CHECK_PATH = f"/openapi/sqlbot/table-permissions/check?mode={mode}"
    settings.PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS = 5.0
    settings.PLATFORM_AUTH_TENANT_ID_HEADER = "tenant-id"


def test_permission_client():
    from common.utils.platform_permission import (
        check_table_permissions,
        denied_tables_to_sse_payload,
        is_permission_check_enabled,
    )

    # disabled → 直接放行
    _force_settings_for_perm(enabled=False)
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds_mock_sales",
        sql="SELECT * FROM orders", tables=["orders"],
    )
    _record("perm/disabled_allows", r.allowed)

    _force_settings_for_perm(enabled=True, mode="allow")
    _record("perm/enabled_helper", is_permission_check_enabled())

    # mode=allow
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds_mock_sales",
        sql="SELECT * FROM users", tables=["users"],
    )
    _record("perm/allow_path", r.allowed and not r.denied_tables)

    # mode=deny → orders 被拒
    _force_settings_for_perm(enabled=True, mode="deny")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds_mock_sales",
        sql="SELECT * FROM orders", tables=["orders", "users"],
    )
    _record(
        "perm/deny_returns_denied_tables",
        (not r.allowed) and any(t.name == "orders" for t in r.denied_tables),
        f"allowed={r.allowed} denied={[t.name for t in r.denied_tables]}",
    )
    sse = denied_tables_to_sse_payload(r, gate_label="gate1")
    _record(
        "perm/sse_payload_shape",
        sse.get("type") == "permission_denied"
        and sse.get("gate") == "gate1"
        and isinstance(sse.get("deniedTables"), list)
        and any(t.get("applyUrl", "").startswith("http") for t in sse["deniedTables"])
        and any(a.get("type") == "permission_apply" for a in sse.get("actions", [])),
        f"sse={sse}",
    )

    # mode=error500 → fail-closed
    _force_settings_for_perm(enabled=True, mode="error500")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="SELECT * FROM orders", tables=["orders"],
    )
    _record(
        "perm/fail_closed_on_5xx",
        (not r.allowed) and (r.reason or "").startswith("permission_service_status_"),
        f"reason={r.reason}",
    )

    # mode=invalid_json → fail-closed
    _force_settings_for_perm(enabled=True, mode="invalid_json")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="SELECT * FROM orders", tables=["orders"],
    )
    _record(
        "perm/fail_closed_on_invalid_json",
        (not r.allowed) and r.reason == "permission_service_invalid_json",
        f"reason={r.reason}",
    )

    # 网络层 timeout（mock sleep > timeout）
    _force_settings_for_perm(enabled=True, mode="timeout")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="SELECT * FROM orders", tables=["orders"],
        timeout=1.0,
    )
    _record(
        "perm/fail_closed_on_timeout",
        (not r.allowed) and (r.reason or "").startswith("permission_service_unreachable"),
        f"reason={r.reason}",
    )

    # 空表名 → 放行（无可校验对象）
    _force_settings_for_perm(enabled=True, mode="allow")
    r = check_table_permissions(
        authorization_header="Bearer x", tenant_id=124, datasource_id="ds",
        sql="", tables=[],
    )
    _record("perm/empty_tables_allows", r.allowed)


# ---- 4. virtual assistant 注入参数形状（不实际跑 ASGI） ----
def test_virtual_assistant_shape():
    """
    不实际跑 ASGI middleware（依赖 Request/SessionDep），仅校验当 PLATFORM_DATASOURCE_ENABLED
    打开时 _inject_platform_assistant_if_enabled 构造的 AssistantHeader 结构是否：
    - type=1（落入 dynamic_ds_types，触发 AssistantOutDs 路径）
    - configuration 含 endpoint/timeout/oid/platform_virtual
    - certificate 含 Authorization+tenant-id 两条 header
    """
    from apps.system.middleware import auth as auth_mod
    from common.core.config import settings
    settings.PLATFORM_DATASOURCE_ENABLED = True
    settings.PLATFORM_DATASOURCE_BASE_URL = MOCK_BASE
    settings.PLATFORM_DATASOURCE_LIST_PATH = "/openapi/sqlbot/datasources/query"

    class _State:
        assistant = None
        current_user = type("U", (), {"oid": 124})()

    class _Req:
        state = _State()

    inst = auth_mod.TokenMiddleware(app=None)
    inst._inject_platform_assistant_if_enabled(_Req(), token_value="Bearer fake-token")
    a = _Req.state.assistant
    if a is None:
        _record("inject/builds_assistant", False, "state.assistant 仍为 None")
        return
    cfg = json.loads(a.configuration)
    cert = json.loads(a.certificate)
    keys_in_cert = {c.get("key") for c in cert}
    _record(
        "inject/type_is_1",
        a.type == 1,
        f"type={a.type}",
    )
    _record(
        "inject/configuration_keys",
        {"endpoint", "timeout", "oid", "platform_virtual"}.issubset(set(cfg.keys())),
        f"cfg={cfg}",
    )
    _record(
        "inject/certificate_has_auth_and_tenant",
        "Authorization" in keys_in_cert and "tenant-id" in keys_in_cert,
        f"keys={keys_in_cert}",
    )


# ---- main ----
def main():
    _ensure_mock_running()
    print(f"== verify against mock at {MOCK_BASE} ==")
    for fn in (test_sql_parser, test_out_ds_factory, test_permission_client, test_virtual_assistant_shape):
        try:
            fn()
        except Exception as e:
            traceback.print_exc()
            _record(fn.__name__, False, f"exception {type(e).__name__}: {e}")

    total = len(results)
    failed = sum(1 for _, ok, _ in results if not ok)
    print()
    print(f"== {total - failed}/{total} passed, {failed} failed ==")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
