#!/usr/bin/env bash
# 在全新 venv 中按 V11/LoongArch64 生产约束运行后端完整测试。
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
TEMP_ROOT="$(mktemp -d)"
cleanup() {
    case "$TEMP_ROOT" in
        /tmp/*) rm -rf -- "$TEMP_ROOT" ;;
        *) printf '拒绝清理异常临时目录：%s\n' "$TEMP_ROOT" >&2 ;;
    esac
}
trap cleanup EXIT

python3 -m venv "$TEMP_ROOT/venv"
"$TEMP_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
    --upgrade 'pip>=25.3,<27'
"$TEMP_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
    --constraint "$ROOT/deploy/constraints-kylin-v11.txt" \
    --build-constraint "$ROOT/deploy/constraints-kylin-v11.txt" \
    --editable "$ROOT/backend[dev]"
"$TEMP_ROOT/venv/bin/python" -m pytest "$ROOT/backend/tests"
