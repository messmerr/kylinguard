#!/usr/bin/env bash
set -Eeuo pipefail

pass() { printf '[PASS] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; FAILED=1; }
FAILED=0

if [[ -x /usr/bin/nkvers ]]; then
    /usr/bin/nkvers || fail "nkvers 执行失败"
else
    fail "缺少 V11 版本证据命令 nkvers"
fi

case "$(uname -m)" in
    loongarch64|loong64) pass "LoongArch64 架构" ;;
    *) fail "当前架构不是 LoongArch64：$(uname -m)" ;;
esac

CONTROL_UID=""
EXEC_UID=""
if CONTROL_UID="$(id -u kylinguard 2>/dev/null)" && [[ "$CONTROL_UID" != "0" ]]; then
    pass "控制面账户非 root"
else
    fail "控制面账户缺失或为 root"
fi
if EXEC_UID="$(id -u kylinguard-exec 2>/dev/null)" && [[ "$EXEC_UID" != "0" ]]; then
    pass "执行面账户非 root"
else
    fail "执行面账户缺失或为 root"
fi
if [[ -n "$CONTROL_UID" && -n "$EXEC_UID" && "$CONTROL_UID" != "$EXEC_UID" ]]; then
    pass "控制面/执行面 UID 分离"
else
    fail "控制面/执行面 UID 未分离"
fi

sudo -u kylinguard -- sudo -n -H -u kylinguard-exec -- \
    /usr/bin/test -w /srv/kylinguard-workspace && pass "受限账户可写工作目录" \
    || fail "受限账户无法写工作目录"
sudo -u kylinguard-exec -- /usr/bin/test ! -r /var/lib/kylinguard/db && \
    pass "执行面无法读取控制面数据库" || fail "数据库未与执行面隔离"
if sudo -u kylinguard -- sudo -n -- /usr/bin/true 2>/dev/null; then
    fail "控制面意外拥有任意 root 命令权限"
else
    pass "控制面不能执行任意 root 命令"
fi

visudo -cf /etc/sudoers >/dev/null && pass "sudoers 语法有效" \
    || fail "sudoers 语法无效"
systemctl is-enabled kylinguard.service >/dev/null && pass "服务已开机启用" \
    || fail "服务未开机启用"
systemctl is-active kylinguard.service >/dev/null && pass "服务正在运行" \
    || fail "服务未运行"

/opt/kylinguard/current/venv/bin/python - <<'PY' && pass "HTTP 健康检查通过" \
    || fail "HTTP 健康检查失败"
import json
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as response:
    assert response.status == 200
    json.load(response)
PY

if ((FAILED)); then
    exit 1
fi
printf '[PASS] KylinGuard 部署边界与运行状态全部通过\n'
