#!/bin/sh
set -eu
: "${SQLBOT_BIND_HOST:?Set private bridge address}"
: "${BACKEND_PORT:?Set API port}"
: "${MCP_PORT:?Set image port}"
: "${G2_PORT:?Set internal renderer port}"
export G2_HOST=127.0.0.1
cd /opt/sqlbot/app
wait-for-it "${POSTGRES_SERVER}:${POSTGRES_PORT}" --timeout=120 --strict -- echo 'PostgreSQL reachable'
node /opt/sqlbot/g2-ssr/app.js &
g2_pid=$!
uvicorn main:mcp_app --host "$SQLBOT_BIND_HOST" --port "$MCP_PORT" &
image_pid=$!
uvicorn main:app --host "$SQLBOT_BIND_HOST" --port "$BACKEND_PORT" --workers 1 --proxy-headers &
app_pid=$!
trap 'kill "$app_pid" "$image_pid" "$g2_pid" 2>/dev/null || true' INT TERM EXIT
wait "$app_pid"

