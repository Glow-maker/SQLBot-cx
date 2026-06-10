#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

VITE_API_BASE_URL=${VITE_API_BASE_URL:-/api/v1}
VITE_PUBLIC_BASE_URL=${VITE_PUBLIC_BASE_URL:-}
VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED=${VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED:-true}
VITE_SHOW_REASONING=${VITE_SHOW_REASONING:-false}

export DOCKER_BUILDKIT=${DOCKER_BUILDKIT:-1}

cd "$PROJECT_ROOT"

echo "Building sqlbot-cx-frontend:latest"
docker build --network=host \
  -t sqlbot-cx-frontend:latest \
  -f deploy/Dockerfile.frontend \
  --build-arg VITE_API_BASE_URL="$VITE_API_BASE_URL" \
  --build-arg VITE_PUBLIC_BASE_URL="$VITE_PUBLIC_BASE_URL" \
  --build-arg VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED="$VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED" \
  --build-arg VITE_SHOW_REASONING="$VITE_SHOW_REASONING" \
  .

cd "$SCRIPT_DIR"
echo "Recreating sqlbot-frontend"
docker compose -f docker-compose.host.yml up -d --force-recreate sqlbot-frontend

echo "Verifying SQLBot frontend"
sh "$SCRIPT_DIR/verify-frontend.sh"
