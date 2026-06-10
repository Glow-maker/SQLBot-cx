#!/bin/sh
set -e

SSR_PATH=/opt/sqlbot/g2-ssr
APP_PATH=/opt/sqlbot/app
PM2_CMD_PATH=$SSR_PATH/node_modules/pm2/bin/pm2

BACKEND_PORT=${BACKEND_PORT:-8011}
MCP_PORT=${MCP_PORT:-8012}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

wait-for-it ${POSTGRES_SERVER}:${POSTGRES_PORT} --timeout=120 --strict -- echo "PostgreSQL ready."

nohup $PM2_CMD_PATH start $SSR_PATH/app.js &
nohup uvicorn main:mcp_app --host 0.0.0.0 --port ${MCP_PORT} &

cd $APP_PATH
exec uvicorn main:app --host 0.0.0.0 --port ${BACKEND_PORT} --workers 1 --proxy-headers
