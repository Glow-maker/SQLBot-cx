#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

export VITE_API_BASE_URL=${VITE_API_BASE_URL:-/api/v1}
export VITE_PUBLIC_BASE_URL=${VITE_PUBLIC_BASE_URL:-/sqlbot-app/}

sh "$SCRIPT_DIR/build-and-up.sh"
