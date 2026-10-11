#!/usr/bin/env bash
set -euo pipefail
test "$(hostname)" != ''
test -d /data/service/dev-1
test -f /data/service/dev-1/sqlbot/backend.env
for port in 20186 20187 20188 20189; do
  if ss -H -lnt "sport = :${port}" | grep -q .; then
    echo "Port ${port} already occupied; inspect before deployment" >&2
    exit 1
  fi
done
docker compose version
echo DEV1_PREFLIGHT_OK
