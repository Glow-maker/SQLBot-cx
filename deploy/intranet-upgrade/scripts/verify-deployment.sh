#!/bin/sh
set -eu
: "${PUBLIC_ORIGIN:?设置中台origin，例如https://portal.intranet，不带结尾斜线}"
for c in curl; do command -v "$c" >/dev/null 2>&1 || exit 2; done
curl --fail --silent --show-error --max-time 20 -o /dev/null "$PUBLIC_ORIGIN/sqlbot/chat/index"
curl --fail --silent --show-error --max-time 20 -o /dev/null "$PUBLIC_ORIGIN/sqlbot-app/"
api_code=$(curl --silent --show-error --max-time 20 -o /dev/null -w '%{http_code}' "$PUBLIC_ORIGIN/api/v1/openapi.json")
case "$api_code" in 200|401) ;; *) echo "SQLBot API异常HTTP $api_code"; exit 2;; esac
printf 'API返回HTTP %s；未带Token时401是认证保护，不代表有效账号可以问数。\n' "$api_code"
printf '%s\n' '三个入口HTTP检查通过；这不能证明登录、微应用挂载、权限或问数成功。'
if [ -n "${SQLBOT_CONTAINER:-}" ]; then
  docker inspect "$SQLBOT_CONTAINER" --format 'image={{.Image}} status={{.State.Status}}'
  docker exec "$SQLBOT_CONTAINER" sh -c 'cd /opt/sqlbot/app && alembic current'
fi
printf '%s\n' '请继续教程中的有权/无权账号、首次/增量同步、SSE和跨源检查。不要把Token放在命令行或测试报告。'
