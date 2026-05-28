#!/bin/sh
set -eu

FRONTEND_URL=${SQLBOT_FRONTEND_URL:-http://127.0.0.1:5173/sqlbot/}
CONTAINER_NAME=${SQLBOT_FRONTEND_CONTAINER:-sqlbot-frontend}
EXPECTED_ENTRY=${SQLBOT_EXPECTED_ENTRY_JS:-}
EXPECTED_ASSET_PREFIX=${SQLBOT_EXPECTED_ASSET_PREFIX:-}
CURL_MAX_TIME=${SQLBOT_CURL_MAX_TIME:-15}
CURL_RETRIES=${SQLBOT_CURL_RETRIES:-10}
BUNDLE_CHECK_BYTES=${SQLBOT_BUNDLE_CHECK_BYTES:-1048576}

TMP_DIR=${TMPDIR:-/tmp}
HTML_FILE="$TMP_DIR/sqlbot-frontend-index.html"
JS_FILE="$TMP_DIR/sqlbot-frontend-entry.js"
XPACK_FILE="$TMP_DIR/sqlbot-frontend-xpack.js"

case "$FRONTEND_URL" in
  */) BASE_URL="$FRONTEND_URL" ;;
  *) BASE_URL="$FRONTEND_URL/" ;;
esac

echo "Checking SQLBot frontend: $BASE_URL"

fetch_with_retry() {
  url=$1
  output=$2
  shift 2
  attempt=1
  while :; do
    if curl -fsSL --max-time "$CURL_MAX_TIME" "$@" "$url" -o "$output"; then
      return 0
    fi
    if [ "$attempt" -ge "$CURL_RETRIES" ]; then
      return 1
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
}

fetch_with_retry "$BASE_URL" "$HTML_FILE"

ENTRY_JS=$(grep -o 'index\.[^"'"'"']*\.js' "$HTML_FILE" | head -n 1 || true)
if [ -z "$ENTRY_JS" ]; then
  echo "FAIL: cannot find index.*.js in $BASE_URL"
  exit 1
fi

echo "Remote entry: $ENTRY_JS"

if [ -n "$EXPECTED_ASSET_PREFIX" ] && ! grep -q "$EXPECTED_ASSET_PREFIX" "$HTML_FILE"; then
  echo "FAIL: frontend entry asset prefix does not match expected prefix: $EXPECTED_ASSET_PREFIX"
  exit 1
fi

if [ -n "$EXPECTED_ENTRY" ] && [ "$ENTRY_JS" != "$EXPECTED_ENTRY" ]; then
  echo "FAIL: remote entry is $ENTRY_JS, expected $EXPECTED_ENTRY"
  exit 1
fi

if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  CONTAINER_ENTRY=$(docker exec "$CONTAINER_NAME" sh -c \
    "grep -o 'index\\.[^\"'\"'\"']*\\.js' /usr/share/nginx/html/sqlbot/index.html | head -n 1" \
    || true)
  if [ -n "$CONTAINER_ENTRY" ]; then
    echo "Container entry: $CONTAINER_ENTRY"
    if [ "$CONTAINER_ENTRY" != "$ENTRY_JS" ]; then
      echo "FAIL: remote nginx is serving $ENTRY_JS, but container has $CONTAINER_ENTRY"
      exit 1
    fi
  else
    echo "WARN: cannot read container entry from $CONTAINER_NAME"
  fi
else
  echo "WARN: docker container $CONTAINER_NAME not found; skip container comparison"
fi

ENTRY_URL=$(grep -o "http[^\"']*/assets/$ENTRY_JS" "$HTML_FILE" | head -n 1 || true)
if [ -z "$ENTRY_URL" ]; then
  ENTRY_URL="${BASE_URL}assets/$ENTRY_JS"
fi

echo "Entry URL: $ENTRY_URL"
RANGE_END=$((BUNDLE_CHECK_BYTES - 1))
fetch_with_retry "$ENTRY_URL" "$JS_FILE" --range "0-$RANGE_END"

if grep -q 'class jb extends tl\|less/lib/less\|MixinDefinition\|NamespaceValue' "$JS_FILE"; then
  echo "FAIL: entry bundle still contains Less runtime fragments that caused the tl initialization error"
  exit 1
fi

XPACK_URL="${BASE_URL}xpack_static/license-generator.umd.js"
echo "Xpack static URL: $XPACK_URL"
fetch_with_retry "$XPACK_URL" "$XPACK_FILE"

if [ ! -s "$XPACK_FILE" ]; then
  echo "FAIL: xpack_static/license-generator.umd.js is missing or empty"
  exit 1
fi

echo "PASS: SQLBot frontend entry is consistent, xpack_static exists, and the known Less runtime fragments are absent."
