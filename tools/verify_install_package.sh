#!/usr/bin/env bash
# 在非目标 Linux/WSL 上验证归档权限位、清单和安装器只读预检。
set -Eeuo pipefail

if (($# < 1 || $# > 2)); then
    printf '用法：%s <KylinGuard-*.tar.gz> [--with-python-install]\n' "$0" >&2
    exit 64
fi
WITH_PYTHON_INSTALL=0
if (($# == 2)); then
    [[ "$2" == "--with-python-install" ]] || {
        printf '未知参数：%s\n' "$2" >&2
        exit 64
    }
    WITH_PYTHON_INSTALL=1
fi

ARCHIVE="$(readlink -f -- "$1")"
[[ -f "$ARCHIVE" ]] || {
    printf '归档不存在：%s\n' "$ARCHIVE" >&2
    exit 66
}

TEMP_ROOT="$(mktemp -d)"
cleanup() {
    case "$TEMP_ROOT" in
        /tmp/*) rm -rf -- "$TEMP_ROOT" ;;
        *) printf '拒绝清理异常临时目录：%s\n' "$TEMP_ROOT" >&2 ;;
    esac
}
trap cleanup EXIT

tar -xzf "$ARCHIVE" -C "$TEMP_ROOT"
PACKAGE_ROOT="$(find "$TEMP_ROOT" -mindepth 1 -maxdepth 1 -type d -print -quit)"
[[ -n "$PACKAGE_ROOT" ]] || {
    printf '归档没有顶层目录\n' >&2
    exit 65
}

for executable in \
    "$PACKAGE_ROOT/install.sh" \
    "$PACKAGE_ROOT/uninstall.sh" \
    "$PACKAGE_ROOT/deploy/kylinguard-privileged"; do
    [[ -x "$executable" ]] || {
        printf '缺少执行位：%s\n' "$executable" >&2
        exit 65
    }
done

stat -c '%a %U:%G %n' \
    "$PACKAGE_ROOT/install.sh" \
    "$PACKAGE_ROOT/uninstall.sh" \
    "$PACKAGE_ROOT/deploy/kylinguard-privileged" \
    "$PACKAGE_ROOT/deploy/kylinguard.service"
"$PACKAGE_ROOT/install.sh" --check-only --allow-non-target

if ((WITH_PYTHON_INSTALL)); then
    python3 -m venv "$TEMP_ROOT/venv"
    "$TEMP_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
        --upgrade 'pip>=25.3,<27'
    WHEEL="$(find "$PACKAGE_ROOT/payload/wheels" -maxdepth 1 -type f \
        -name 'kylinguard-*.whl' -print -quit)"
    "$TEMP_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
        --constraint "$PACKAGE_ROOT/deploy/constraints-kylin-v11.txt" \
        --build-constraint "$PACKAGE_ROOT/deploy/constraints-kylin-v11.txt" \
        "$WHEEL"
    install -d "$TEMP_ROOT/state" "$TEMP_ROOT/workspace"
    "$TEMP_ROOT/venv/bin/python" - \
        "$TEMP_ROOT/state/kylinguard.db" \
        "$PACKAGE_ROOT/payload/frontend" \
        "$TEMP_ROOT/workspace" <<'PY'
import asyncio
import sys

import httpx

from kylinguard.api import create_app
from kylinguard.config import Settings


async def main():
    app = create_app(
        Settings(
            _env_file=None,
            db_path=sys.argv[1],
            frontend_dist=sys.argv[2],
            workspace_root=sys.argv[3],
        ),
        with_tools=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as client:
        health = await client.get("/api/health")
        index = await client.get("/")
    assert health.status_code == 200
    assert index.status_code == 200
    assert b"<div id=\"app\"></div>" in index.content


asyncio.run(main())
print("[PASS] wheel 在线依赖安装、应用工厂和前端挂载通过")
PY
fi

printf '[PASS] 安装归档 Linux 只读预检通过\n'
