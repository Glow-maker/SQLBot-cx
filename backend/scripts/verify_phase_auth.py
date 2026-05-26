"""
主线一（token 访问）契约级自验。

每条用例都按「传入 / 期望 / 实际」三栏记录，跑完后：
- stdout 打 PASS/FAIL
- 同时写一份 markdown 表格到 docs/integration/变更/Phase-主线一-验证报告.md
  作为可直接交付的报告（gitignored）

不连真 Redis、不连中台：
- token_resolver 用 _FakeRedis 替换底层 client
- 中台 HTTP（tenant-by-name）走 mock_platform（127.0.0.1:9999）

依赖：
- 终端 A: python scripts/mock_platform.py        (监听 127.0.0.1:9999)
- 运行:   python scripts/verify_phase_auth.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 按 main.py 顺序避循环依赖
import sqlbot_xpack  # noqa: E402,F401
import apps.api  # noqa: E402,F401

MOCK_BASE = os.environ.get("MOCK_BASE", "http://127.0.0.1:9999")
REPORT_OUT = os.environ.get(
    "REPORT_OUT",
    os.path.abspath(
        os.path.join(_BACKEND, os.pardir, "docs", "integration", "变更", "Phase-主线一-验证报告.md")
    ),
)


# 每条用例的记录：name / 输入摘要 / 期望摘要 / 实际摘要 / 是否通过
records: list[dict[str, Any]] = []


def _short(v: Any, lim: int = 200) -> str:
    s = repr(v) if not isinstance(v, str) else v
    return (s[: lim - 3] + "...") if len(s) > lim else s


def case(name: str, given: Any, expected: Any, actual: Any, ok: bool, group: str = "") -> None:
    rec = {
        "group": group,
        "name": name,
        "given": _short(given),
        "expected": _short(expected),
        "actual": _short(actual),
        "ok": ok,
    }
    records.append(rec)
    tag = "[PASS]" if ok else "[FAIL]"
    if ok:
        print(f"{tag} {name}")
    else:
        print(f"{tag} {name}\n        given={rec['given']}\n     expected={rec['expected']}\n       actual={rec['actual']}")


# =====================================================================
# Group 1: token_resolver 纯函数
# =====================================================================
def test_pure_helpers():
    from common.core import token_resolver as tr
    g = "1. token_resolver 纯函数"

    # _normalize_redis_key
    tr.settings.PLATFORM_AUTH_REDIS_KEY_PREFIX = "oauth2_access_token:%s"
    actual = tr._normalize_redis_key("abc")
    case("key/template_substitute",
         "PREFIX='oauth2_access_token:%s', token='abc'",
         "oauth2_access_token:abc",
         actual, actual == "oauth2_access_token:abc", g)

    tr.settings.PLATFORM_AUTH_REDIS_KEY_PREFIX = "prefix:"
    actual = tr._normalize_redis_key("abc")
    case("key/prefix_concat",
         "PREFIX='prefix:', token='abc'", "prefix:abc",
         actual, actual == "prefix:abc", g)
    tr.settings.PLATFORM_AUTH_REDIS_KEY_PREFIX = "oauth2_access_token:%s"

    # _to_int
    cases = [(None, None), (True, None), ("42", 42), ("x", None), (3.7, 3)]
    for inp, exp in cases:
        actual = tr._to_int(inp)
        case(f"to_int({inp!r})", inp, exp, actual, actual == exp, g)

    # _parse_datetime
    s = 1_700_000_000
    ms = s * 1000
    a, b = tr._parse_datetime(s), tr._parse_datetime(ms)
    case("parse_dt/seconds_eq_milliseconds",
         f"sec={s}, ms={ms}", "两者解析后相等",
         f"sec_dt={a}, ms_dt={b}", a is not None and b is not None and a == b, g)

    iso = "2026-05-07T12:00:00"
    actual = tr._parse_datetime(iso)
    case("parse_dt/iso8601", iso, datetime(2026, 5, 7, 12, 0, 0),
         actual, actual == datetime(2026, 5, 7, 12, 0, 0), g)

    case("parse_dt/empty_string", "''", None,
         tr._parse_datetime(""), tr._parse_datetime("") is None, g)

    # _is_expired
    past = datetime.now() - timedelta(hours=1)
    fut = datetime.now() + timedelta(hours=1)
    case("is_expired/past_true", "1 小时前", True, tr._is_expired(past), tr._is_expired(past), g)
    case("is_expired/future_false", "1 小时后", False, tr._is_expired(fut), not tr._is_expired(fut), g)

    # _parse_json_object
    actual = tr._parse_json_object('{"a":1}')
    case("parse_json/plain_object", '{"a":1}', {"a": 1}, actual, actual == {"a": 1}, g)

    actual = tr._parse_json_object('"{\\"a\\":1}"')
    case("parse_json/double_encoded",
         '"{\\"a\\":1}"  (JSON 字符串包着一个 JSON 对象，中台 Redis 实测形态)',
         {"a": 1}, actual, actual == {"a": 1}, g)

    actual = tr._parse_json_object("not json")
    case("parse_json/bad_input", "'not json'", None, actual, actual is None, g)

    # _extract_account
    tr.settings.PLATFORM_AUTH_USERINFO_ACCOUNT_FIELD = "username"
    actual = tr._extract_account({"username": "alice"})
    case("extract_account/by_username", {"username": "alice"}, "alice", actual, actual == "alice", g)
    actual = tr._extract_account({"account": "bob"})
    case("extract_account/fallback_account", {"account": "bob"}, "bob", actual, actual == "bob", g)
    actual = tr._extract_account({"username": "   "})
    case("extract_account/strip_blank", {"username": "   "}, None, actual, actual is None, g)


# =====================================================================
# Group 2: _build_platform_identity（中台 Redis token 记录 → identity）
# =====================================================================
def test_build_identity():
    from common.core import token_resolver as tr
    g = "2. token 记录 → PlatformTokenIdentity"

    tr.settings.PLATFORM_AUTH_USERINFO_ACCOUNT_FIELD = "username"
    tr.settings.PLATFORM_AUTH_REQUIRE_USER_TYPE = 2

    base = {
        "userId": 101,
        "tenantId": 124,
        "userType": 2,
        "expiresTime": int((datetime.now() + timedelta(hours=1)).timestamp() * 1000),
        "userInfo": {"username": "alice", "nickname": "Alice"},
    }
    ident = tr._build_platform_identity(base)
    ok = (ident is not None and ident.user_id == 101 and ident.account == "alice"
          and ident.tenant_id == 124 and ident.nickname == "Alice")
    case("identity/basic_ok",
         given={"userId": 101, "tenantId": 124, "userType": 2, "userInfo": {...}},
         expected="user_id=101, account='alice', tenant_id=124",
         actual=f"user_id={getattr(ident,'user_id',None)}, account={getattr(ident,'account',None)}, tenant_id={getattr(ident,'tenant_id',None)}",
         ok=ok, group=g)

    expired = {**base, "expiresTime": int((datetime.now() - timedelta(hours=1)).timestamp() * 1000)}
    actual = tr._build_platform_identity(expired)
    case("identity/expired_rejected",
         "expiresTime 1 小时前", "返回 None（拒绝）",
         actual, actual is None, g)

    wrong_type = {**base, "userType": 1}
    actual = tr._build_platform_identity(wrong_type)
    case("identity/wrong_user_type_rejected",
         "REQUIRE_USER_TYPE=2, 实际 userType=1",
         "返回 None（拒绝）",
         actual, actual is None, g)

    tr.settings.PLATFORM_AUTH_REQUIRE_USER_TYPE = None
    ident = tr._build_platform_identity({**base, "userType": 99})
    case("identity/no_user_type_restriction",
         "REQUIRE_USER_TYPE=None, userType=99",
         "返回 identity（不拒绝）",
         "ident is not None" if ident else "None", ident is not None, g)
    tr.settings.PLATFORM_AUTH_REQUIRE_USER_TYPE = 2

    stringified = {**base, "userInfo": json.dumps({"username": "alice"})}
    ident = tr._build_platform_identity(stringified)
    case("identity/userinfo_as_string",
         "userInfo 是 JSON 字符串", "account='alice'",
         f"account={getattr(ident,'account',None)}",
         ident is not None and ident.account == "alice", g)

    double = {**base, "userInfo": json.dumps(json.dumps({"username": "alice"}))}
    ident = tr._build_platform_identity(double)
    case("identity/userinfo_double_json",
         "userInfo 双重 JSON 编码", "account='alice'",
         f"account={getattr(ident,'account',None)}",
         ident is not None and ident.account == "alice", g)

    oid_only = {k: v for k, v in base.items() if k != "tenantId"}
    oid_only["oid"] = 99
    ident = tr._build_platform_identity(oid_only)
    case("identity/oid_fallback_when_no_tenantId",
         "无 tenantId, oid=99", "tenant_id=99",
         f"tenant_id={getattr(ident,'tenant_id',None)}",
         ident is not None and ident.tenant_id == 99, g)

    missing_uid = {**base}
    missing_uid.pop("userId")
    actual = tr._build_platform_identity(missing_uid)
    case("identity/missing_userid", "无 userId", None, actual, actual is None, g)

    no_account = {**base, "userInfo": {}}
    actual = tr._build_platform_identity(no_account)
    case("identity/missing_account", "userInfo={}", None, actual, actual is None, g)


# =====================================================================
# Group 3: resolve_platform_token_identity（fake redis）
# =====================================================================
class _FakeRedis:
    def __init__(self, store: dict[str, str]):
        self._store = store

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


def test_resolve_with_fake_redis():
    from common.core import token_resolver as tr
    g = "3. resolve_platform_token_identity（async + fake Redis）"

    good = "tok-good-abcdef"
    miss = "tok-miss-zz"
    record = {
        "userId": 1,
        "tenantId": 124,
        "userType": 2,
        "expiresTime": int((datetime.now() + timedelta(hours=1)).timestamp() * 1000),
        "userInfo": {"username": "admin", "nickname": "Admin"},
    }
    key = tr._normalize_redis_key(good)
    fake = _FakeRedis({key: json.dumps(record)})

    tr.settings.PLATFORM_AUTH_ENABLED = True
    tr.settings.PLATFORM_AUTH_REDIS_URL = "redis://fake:6379/0"
    tr._platform_auth_redis_client = fake

    async def _r(t: str):
        return await tr.resolve_platform_token_identity(t)

    ident = asyncio.run(_r(good))
    case("resolve/hit_token",
         f"token={good}, fake redis 里有对应记录",
         "返回 PlatformTokenIdentity, account='admin', user_id=1",
         f"account={getattr(ident,'account',None)}, user_id={getattr(ident,'user_id',None)}",
         ident is not None and ident.account == "admin" and ident.user_id == 1, g)

    ident = asyncio.run(_r(miss))
    case("resolve/miss_token",
         f"token={miss}, fake redis 里没有",
         "返回 None",
         ident, ident is None, g)

    tr.settings.PLATFORM_AUTH_ENABLED = False
    tr._platform_auth_redis_client = None
    ident = asyncio.run(_r(good))
    case("resolve/disabled_skips_redis",
         "PLATFORM_AUTH_ENABLED=false",
         "直接返回 None（不走 Redis）",
         ident, ident is None, g)
    tr.settings.PLATFORM_AUTH_ENABLED = True
    tr._platform_auth_redis_client = fake


# =====================================================================
# Group 4: _build_user_from_platform_token（admin 推断）
# =====================================================================
def test_build_user_from_platform_token():
    from apps.system.middleware import auth as auth_mod
    from common.core.token_resolver import PlatformTokenIdentity
    g = "4. PlatformTokenIdentity → UserInfoDTO（admin 推断）"

    mid = auth_mod.TokenMiddleware(app=None)

    a = PlatformTokenIdentity(user_id=999, account="admin", tenant_id=124, nickname="Admin", payload={})
    b = PlatformTokenIdentity(user_id=1, account="alice", tenant_id=124, nickname="Alice", payload={})
    c = PlatformTokenIdentity(user_id=42, account="bob", tenant_id=124, nickname="Bob", payload={})

    u = mid._build_user_from_platform_token(a)
    case("build_user/admin_by_account_name",
         "account='admin', user_id=999",
         "isAdmin=True, weight=1, oid=124",
         f"isAdmin={u.isAdmin}, weight={u.weight}, oid={u.oid}",
         u.isAdmin is True and u.weight == 1 and u.oid == 124, g)

    u = mid._build_user_from_platform_token(b)
    case("build_user/admin_by_uid_1",
         "account='alice', user_id=1",
         "isAdmin=True（uid==1 即 admin）",
         f"isAdmin={u.isAdmin}",
         u.isAdmin is True, g)

    u = mid._build_user_from_platform_token(c)
    case("build_user/normal_user_not_admin",
         "account='bob', user_id=42",
         "isAdmin=False, weight=0",
         f"isAdmin={u.isAdmin}, weight={u.weight}",
         u.isAdmin is False and u.weight == 0, g)

    case("build_user/email_placeholder",
         "中台 token 不带 email",
         "email 形如 '<account>@platform.local'",
         u.email,
         u.email.endswith("@platform.local"), g)


# =====================================================================
# Group 5: 本地 admin 白名单
# =====================================================================
def test_local_admin_whitelist():
    from apps.system.middleware import auth as auth_mod
    from common.core.config import settings
    g = "5. 本地 admin 白名单（双通道）"

    mid = auth_mod.TokenMiddleware(app=None)
    settings.PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN = True
    settings.PLATFORM_AUTH_LOCAL_ADMIN_ACCOUNTS = "admin, ops, root"

    actual = mid._get_local_admin_accounts()
    case("whitelist/parse_set",
         "'admin, ops, root'", {"admin", "ops", "root"},
         actual, actual == {"admin", "ops", "root"}, g)

    case("whitelist/admin_hit", "'admin'", True,
         mid._is_local_admin_account("admin"), mid._is_local_admin_account("admin"), g)
    case("whitelist/case_insensitive", "'ADMIN'", True,
         mid._is_local_admin_account("ADMIN"), mid._is_local_admin_account("ADMIN"), g)
    case("whitelist/miss", "'alice'", False,
         mid._is_local_admin_account("alice"), not mid._is_local_admin_account("alice"), g)
    case("whitelist/empty_account", "''", False,
         mid._is_local_admin_account(""), not mid._is_local_admin_account(""), g)

    settings.PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN = False
    case("whitelist/disabled_rejects_even_admin",
         "ALLOW_LOCAL_ADMIN_LOGIN=false, account='admin'",
         False,
         mid._is_local_admin_account("admin"),
         not mid._is_local_admin_account("admin"), g)
    settings.PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN = True


# =====================================================================
# Group 6: _resolve_platform_tenant_id（走 mock HTTP）
# =====================================================================
def _ensure_mock_running():
    import urllib.request
    try:
        urllib.request.urlopen(f"{MOCK_BASE}/openapi/sqlbot/datasources/query", timeout=2).read()
    except Exception as e:
        print(f"!! mock 不可达 ({MOCK_BASE}) : {e}")
        print("   请先启动: python scripts/mock_platform.py")
        sys.exit(1)


def test_tenant_id_resolution():
    from common.core.config import settings
    from apps.system.api import login as login_mod
    import httpx
    g = "6. tenant-id 解析（走 mock HTTP）"

    settings.PLATFORM_AUTH_BASE_URL = MOCK_BASE
    settings.PLATFORM_AUTH_TENANT_ID_BY_NAME_PATH = "/admin-api/system/tenant/get-id-by-name"
    settings.PLATFORM_AUTH_FIXED_TENANT_ID = None
    settings.PLATFORM_AUTH_FIXED_TENANT_NAME = "八院"
    settings.PLATFORM_AUTH_HTTP_TIMEOUT_SECONDS = 5.0

    async def _run():
        async with httpx.AsyncClient(base_url=MOCK_BASE, timeout=5.0) as client:
            return await login_mod._resolve_platform_tenant_id(client)

    tid = asyncio.run(_run())
    case("tenant/resolved_from_mock",
         "FIXED_TENANT_ID=None, FIXED_TENANT_NAME='八院', mock 返回 124",
         124, tid, tid == 124, g)

    settings.PLATFORM_AUTH_FIXED_TENANT_ID = 999
    tid = asyncio.run(_run())
    case("tenant/fixed_wins_over_name",
         "FIXED_TENANT_ID=999（同时配了 NAME）",
         999, tid, tid == 999, g)
    settings.PLATFORM_AUTH_FIXED_TENANT_ID = None


# =====================================================================
# 输出 markdown 报告
# =====================================================================
PLATFORM_AUTH_REQUIREMENTS = """## 四、需要数据中台后端开发 / 对齐的内容

> 主线一**已落地**，下面列的是"已经在用、但需要中台同事确认契约不要漂移"的项，以及
> P1/P2 的可选增强。本次自验通过 fake Redis 模拟，真接口契约一致即可保证生产链路不破。

### 4.1 已经在用（中台**保持不变**即可，但请确认）

| # | 中台资源 | SQLBot 怎么用 | 期望中台保持 |
|---|---|---|---|
| 1 | OAuth2 access_token 写入 Redis | SQLBot 用 token 直接 `GET key` 拉用户上下文 | key 形如 `oauth2_access_token:<token>`；可配 |
| 2 | Redis value 是 JSON，含 `userId` / `tenantId` / `userType` / `expiresTime` / `userInfo` | `_build_platform_identity` 解析 | 这 5 个字段名不变；`userInfo` 可以是对象或字符串（双重 JSON 也兼容） |
| 3 | `userInfo.username` | `_extract_account` 取 account | 字段名 `username`（已通过 `PLATFORM_AUTH_USERINFO_ACCOUNT_FIELD` 可改） |
| 4 | `userType=2` 表示普通业务用户 | `PLATFORM_AUTH_REQUIRE_USER_TYPE=2` 过滤系统用户 | 数值含义保持 |
| 5 | `expiresTime` 毫秒级时间戳 | `_parse_datetime` 自动判别 s/ms | 1e12 以上视为毫秒（兼容秒级） |
| 6 | OAuth2 登录接口 `/admin-api/system/auth/login` | SQLBot 登录代理调用，把账号密码透传给中台换取 token | path 不变；返回结构含 access_token / userId / tenantId |
| 7 | 租户 id 查询 `/admin-api/system/tenant/get-id-by-name?name=...` | 配了 `PLATFORM_AUTH_FIXED_TENANT_NAME` 但没固定 ID 时使用 | 路径与返回 `{code:0, data:<int>}` 不变 |

### 4.2 主线一 P1（可选增强，提升体验但不阻塞）

| # | 缺口 | 建议中台提供 |
|---|---|---|
| P1-1 | 用户 token 过期前端只能重登 | 提供 `/admin-api/system/auth/refresh-token`（可能已有），SQLBot 后端做 refresh 代理 |
| P1-2 | SQLBot `/login/logout` 只清本地 cache，不会通知中台失效 token | 让 SQLBot 调中台 `/admin-api/system/auth/logout` |
| P1-3 | Redis 抖动 = 全员 401（无回退） | 提供 `/admin-api/system/auth/check-token` 之类的接口，作为 Redis miss 后的兜底 |

### 4.3 主线一 P2（增强 / 加固）

| # | 缺口 | 建议 |
|---|---|---|
| P2-1 | `isAdmin` 推断粗糙：仅基于 `account=='admin' or user_id==1` | 中台返回完整角色 / scopes，SQLBot 据此映射；待中台用户中心 API 上线 |
| P2-2 | 部门 / 工作空间映射 | 中台 `tenantId → 部门` 元数据 API，便于 SQLBot 后续做 ws 隔离 |
"""


def write_markdown_report():
    total = len(records)
    failed = sum(1 for r in records if not r["ok"])
    passed = total - failed

    lines: list[str] = []
    lines.append("# 主线一（认证 / token 访问）验证报告")
    lines.append("")
    lines.append(f"> 自动生成自 `backend/scripts/verify_phase_auth.py`，{datetime.now():%Y-%m-%d %H:%M:%S}")
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
    for r in records:
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
        if r is records[-1] or records[records.index(r) + 1]["group"] != cur_group:
            lines.append("")

    lines.append(PLATFORM_AUTH_REQUIREMENTS)
    lines.append("")

    lines.append("## 五、复现步骤")
    lines.append("")
    lines.append("```powershell")
    lines.append("conda activate sqlbot")
    lines.append("cd H:\\light\\project\\SQLBot-cx\\backend")
    lines.append("# 终端 A:")
    lines.append("python scripts/mock_platform.py")
    lines.append("# 终端 B:")
    lines.append("python scripts/verify_phase_auth.py")
    lines.append("```")
    lines.append("")

    os.makedirs(os.path.dirname(REPORT_OUT), exist_ok=True)
    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入: {REPORT_OUT}")


def main():
    _ensure_mock_running()
    print(f"== verify main-line 1 (auth) against mock at {MOCK_BASE} ==")
    for fn in (
        test_pure_helpers,
        test_build_identity,
        test_resolve_with_fake_redis,
        test_build_user_from_platform_token,
        test_local_admin_whitelist,
        test_tenant_id_resolution,
    ):
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
