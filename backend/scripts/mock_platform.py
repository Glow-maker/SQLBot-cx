"""
Mock 中台 data + 权限服务（Phase 2b / 3b 联调前自测用）。

仅用 Python stdlib，不引入新依赖；运行后监听 127.0.0.1:9999，覆盖两条 SQLBot 实际会调的路径：

  GET  /openapi/sqlbot/datasources/query        —— assistant.py:136 用 GET
  POST /openapi/sqlbot/table-permissions/check  —— platform_permission.py 用 POST

行为通过环境变量切换：

  MOCK_PERMISSION_MODE = allow | deny | error500 | invalid_json | timeout
                         （默认 allow；deny 会返 deniedTables=[orders, payments]）
  MOCK_PERMISSION_DENY_TABLES = "orders,payments"   # deny 模式下的拒绝表
  MOCK_DATASOURCE_FAIL = 1   # 让 ds 接口直接 500，验证 fail-closed
  MOCK_TIMEOUT_SECONDS = 60  # mode=timeout 时的睡眠时长

启动:
  python scripts/mock_platform.py
  PORT=8888 python scripts/mock_platform.py     # 改端口

退出: Ctrl+C
"""
from __future__ import annotations

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "9999"))

logging.basicConfig(
    format="%(asctime)s [mock] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("mock_platform")


def _mode(query: dict | None = None) -> str:
    """Query 中的 mode 优先于环境变量（方便 verify 脚本不重启 mock 即可切换行为）。"""
    if query and "mode" in query:
        return str(query["mode"]).lower()
    return os.environ.get("MOCK_PERMISSION_MODE", "allow").lower()


def _deny_tables() -> list[str]:
    raw = os.environ.get("MOCK_PERMISSION_DENY_TABLES", "orders,payments")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _fixed_datasource() -> dict:
    """
    返回一个对得上 AssistantOutDsSchema 的固定 ds（system_schema.py:189）。
    NOTE: AssistantOutDsSchema.id 当前是 Optional[int]——这就是中台 OpenAPI 协议
    review 时需要对齐的点之一：中台实际可能用字符串 id（如 'ds_sales'），SQLBot
    schema 需要相应放宽到 Optional[int|str]。本 mock 用 int 以便契约自验通过。
    """
    return {
        "id": 9001,
        "name": "Mock 销售数仓",
        "type": "pg",
        "type_name": "PostgreSQL",
        "description": "mock 中台返回的固定数据源",
        "host": "127.0.0.1",
        "port": 5432,
        "dataBase": "sqlbot",
        "user": "root",
        "password": "Password123@pg",
        "db_schema": "public",
        "tables": [
            {
                "name": "orders",
                "comment": "订单主表",
                "fields": [
                    {"name": "id", "type": "bigint", "comment": "主键"},
                    {"name": "user_id", "type": "bigint", "comment": "用户"},
                    {"name": "amount", "type": "numeric", "comment": "金额"},
                    {"name": "created_at", "type": "timestamp", "comment": "创建时间"},
                ],
            },
            {
                "name": "users",
                "comment": "用户表",
                "fields": [
                    {"name": "id", "type": "bigint"},
                    {"name": "name", "type": "varchar"},
                    {"name": "department", "type": "varchar"},
                ],
            },
        ],
    }


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:  # 收敛默认日志
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict | str) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if isinstance(payload, dict) else payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_raw": raw.decode("utf-8", errors="replace")}

    # GET /openapi/sqlbot/datasources/query
    def do_GET(self) -> None:
        if self.path.startswith("/openapi/sqlbot/datasources/query"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            fail_flag = (qs.get("fail", [""])[0] == "1") or (os.environ.get("MOCK_DATASOURCE_FAIL") == "1")
            if fail_flag:
                log.info("ds query -> 500 (forced)")
                self._send_json(500, {"code": 500, "message": "mock forced failure"})
                return
            log.info("ds query -> 200 (1 ds)")
            self._send_json(200, {"code": 0, "data": [_fixed_datasource()]})
            return
        # 主线一：tenant-id by name（中台 OAuth2 登录前置查询）
        if self.path.startswith("/admin-api/system/tenant/get-id-by-name"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            name = qs.get("name", [""])[0]
            log.info("tenant-by-name name=%s -> 124", name)
            self._send_json(200, {"code": 0, "data": 124})
            return
        self._send_json(404, {"code": 404, "message": f"unknown path {self.path}"})

    # POST /openapi/sqlbot/table-permissions/check
    def do_POST(self) -> None:
        if not self.path.startswith("/openapi/sqlbot/table-permissions/check"):
            self._send_json(404, {"code": 404, "message": f"unknown path {self.path}"})
            return

        # 解析 query string（用于切 mode，不依赖环境变量在跨进程同步）
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        query_flat = {k: v[0] for k, v in qs.items() if v}

        body = self._read_body()
        tables_in = [t.get("name") if isinstance(t, dict) else t for t in (body.get("tables") or [])]
        mode = _mode(query_flat)
        log.info("perm check tables=%s mode=%s", tables_in, mode)

        if mode == "error500":
            self._send_json(500, {"code": 500, "message": "mock permission service error"})
            return
        if mode == "invalid_json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"<<<not json>>>")
            return
        if mode == "timeout":
            time.sleep(int(os.environ.get("MOCK_TIMEOUT_SECONDS", "60")))
            self._send_json(200, {"code": 0, "data": {"allowed": True}})
            return

        deny_set = set(_deny_tables())
        if mode == "deny":
            denied = [
                {
                    "name": t,
                    "displayName": f"{t} 表",
                    "catalogPath": f"sales/{t}",
                    "reason": "no permission",
                    "applyUrl": f"http://platform.local/apply?table={t}",
                }
                for t in tables_in
                if t in deny_set
            ]
            allowed_flag = len(denied) == 0
            self._send_json(
                200,
                {
                    "code": 0,
                    "data": {
                        "allowed": allowed_flag,
                        "deniedTables": denied,
                        "datasourceId": body.get("datasourceId"),
                    },
                },
            )
            return

        # allow（默认）
        self._send_json(
            200,
            {
                "code": 0,
                "data": {
                    "allowed": True,
                    "deniedTables": [],
                    "datasourceId": body.get("datasourceId"),
                },
            },
        )


def main() -> None:
    addr = ("127.0.0.1", PORT)
    httpd = ThreadingHTTPServer(addr, Handler)
    log.info(
        "mock platform listening on http://%s:%d (mode=%s, deny=%s)",
        *addr,
        _mode(),
        _deny_tables(),
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
        httpd.shutdown()


if __name__ == "__main__":
    main()
