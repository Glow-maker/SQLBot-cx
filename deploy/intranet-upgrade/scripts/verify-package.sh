#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
command -v sha256sum >/dev/null 2>&1 || { echo '缺少sha256sum'; exit 2; }
sha256sum -c SHA256SUMS
