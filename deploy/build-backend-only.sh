#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

RUNTIME_DOCKERFILE=${SQLBOT_RUNTIME_DOCKERFILE:-}
PYPI_INDEX_URL=${SQLBOT_PYPI_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}

if [ -z "$RUNTIME_DOCKERFILE" ]; then
  if find "$PROJECT_ROOT/deploy/models" -mindepth 1 ! -name .gitkeep -print -quit | grep -q .; then
    RUNTIME_DOCKERFILE=deploy/Dockerfile.runtime-local-model
  else
    RUNTIME_DOCKERFILE=Dockerfile
  fi
fi

export DOCKER_BUILDKIT=${DOCKER_BUILDKIT:-1}

cd "$PROJECT_ROOT"

echo "Building sqlbot-cx-runtime:latest with $RUNTIME_DOCKERFILE"
docker build --network=host \
  --build-arg SQLBOT_PYPI_INDEX_URL="$PYPI_INDEX_URL" \
  -t sqlbot-cx-runtime:latest \
  -f "$RUNTIME_DOCKERFILE" .

echo "Building sqlbot-cx-backend-host:latest"
docker build \
  -t sqlbot-cx-backend-host:latest \
  -f deploy/Dockerfile.backend-host .

cd "$SCRIPT_DIR"
mkdir -p data/sqlbot/excel data/sqlbot/file data/sqlbot/images data/sqlbot/logs

echo "Recreating sqlbot-backend"
docker compose -f docker-compose.host.yml up -d --force-recreate sqlbot-backend

echo "Done. View logs with:"
echo "  docker logs -f sqlbot-backend"
