#!/usr/bin/env bash
# KylinGuard 银河麒麟 V11 / LoongArch64 安装器。
set -Eeuo pipefail
umask 027

readonly CONTROL_USER="kylinguard"
readonly CONTROL_GROUP="kylinguard"
readonly EXEC_USER="kylinguard-exec"
readonly EXEC_GROUP="kylinguard-exec"
readonly WORKSPACE_GROUP="kylinguard-workspace"
readonly APP_ROOT="/opt/kylinguard"
readonly STATE_ROOT="/var/lib/kylinguard"
readonly EXEC_HOME="/var/lib/kylinguard-exec"
readonly WORKSPACE_ROOT="/srv/kylinguard-workspace"
readonly CONFIG_ROOT="/etc/kylinguard"
readonly UNIT_PATH="/etc/systemd/system/kylinguard.service"
readonly SUDOERS_PATH="/etc/sudoers.d/kylinguard"
readonly HELPER_PATH="/usr/local/libexec/kylinguard/kylinguard-privileged"

BUNDLE_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ALLOW_NON_TARGET=0
CHECK_ONLY=0
INSTALL_SYSTEM_DEPS=1
START_SERVICE=1
RELEASE_TMP=""
SUDOERS_CHECK_TMP=""

log() {
    printf '[KylinGuard] %s\n' "$*"
}

die() {
    printf '[KylinGuard] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：sudo ./install.sh [选项]

  --check-only          只校验安装包、平台和基础命令，不修改系统
  --skip-system-deps    不执行 dnf install；仅适用于依赖已预装的目标机
  --no-start            安装并设为开机启动，但本次不启动服务
  --allow-non-target    允许在非银河麒麟 V11/非 LoongArch64 主机做开发验证
  -h, --help            显示帮助

目标机联网依赖默认通过当前已配置的 dnf 和 pip 源安装；安装器不会改写系统
repo。若使用内网 PyPI，请在 sudo 时显式传入 PIP_INDEX_URL/PIP_TRUSTED_HOST。
EOF
}

cleanup() {
    if [[ -n "$RELEASE_TMP" && -d "$RELEASE_TMP" ]]; then
        rm -rf -- "$RELEASE_TMP"
    fi
    if [[ -n "$SUDOERS_CHECK_TMP" && -f "$SUDOERS_CHECK_TMP" ]]; then
        rm -f -- "$SUDOERS_CHECK_TMP"
    fi
}
trap cleanup EXIT

while (($#)); do
    case "$1" in
        --check-only) CHECK_ONLY=1 ;;
        --skip-system-deps) INSTALL_SYSTEM_DEPS=0 ;;
        --no-start) START_SERVICE=0 ;;
        --allow-non-target) ALLOW_NON_TARGET=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
    shift
done

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"
}

verify_bundle() {
    [[ -f "$BUNDLE_ROOT/VERSION" ]] || die "安装包缺少 VERSION"
    [[ -f "$BUNDLE_ROOT/SHA256SUMS" ]] || die "安装包缺少 SHA256SUMS"
    [[ -f "$BUNDLE_ROOT/deploy/kylinguard.service" ]] || die "安装包缺少 systemd unit"
    [[ -f "$BUNDLE_ROOT/deploy/kylinguard.env" ]] || die "安装包缺少环境配置"
    [[ -f "$BUNDLE_ROOT/deploy/sudoers-kylinguard" ]] || die "安装包缺少 sudoers"
    [[ -f "$BUNDLE_ROOT/deploy/kylinguard-privileged" ]] || die "安装包缺少特权 helper"
    [[ -f "$BUNDLE_ROOT/deploy/constraints-kylin-v11.txt" ]] || die "安装包缺少依赖约束"
    [[ -f "$BUNDLE_ROOT/payload/frontend/index.html" ]] || die "安装包缺少前端构建结果"

    local wheel_count
    wheel_count="$(find "$BUNDLE_ROOT/payload/wheels" -maxdepth 1 -type f \
        -name 'kylinguard-*.whl' -print 2>/dev/null | wc -l)"
    [[ "$wheel_count" == "1" ]] || \
        die "payload/wheels 中必须恰好有一个 KylinGuard wheel"

    (
        cd -- "$BUNDLE_ROOT"
        sha256sum -c SHA256SUMS
    ) || die "安装包完整性校验失败"
}

platform_evidence() {
    {
        command -v nkvers >/dev/null 2>&1 && nkvers 2>/dev/null || true
        [[ -r /etc/os-release ]] && cat /etc/os-release || true
    } | tr '[:upper:]' '[:lower:]'
}

verify_target_platform() {
    local evidence arch is_kylin is_v11
    evidence="$(platform_evidence)"
    arch="$(uname -m)"
    is_kylin=0
    is_v11=0
    case "$evidence" in
        *kylin*|*麒麟*) is_kylin=1 ;;
    esac
    case "$evidence" in
        *v11*|*version_id=11*|*version_id=\"11\"*) is_v11=1 ;;
    esac

    if ((is_kylin == 0 || is_v11 == 0)); then
        if ((ALLOW_NON_TARGET == 0)); then
            die "仅支持银河麒麟高级服务器操作系统 V11；可用 nkvers 核对版本"
        fi
        log "警告：已允许在非银河麒麟 V11 主机进行开发验证"
    fi

    case "$arch" in
        loongarch64|loong64) ;;
        *)
            if ((ALLOW_NON_TARGET == 0)); then
                die "本安装包目标架构为 LoongArch64，当前为 $arch"
            fi
            log "警告：已允许在非 LoongArch64 架构 $arch 上进行开发验证"
            ;;
    esac
    local nkvers_line
    nkvers_line="$(
        command -v nkvers >/dev/null 2>&1 && nkvers 2>/dev/null | head -n 1 \
            || printf '不可用'
    )"
    log "平台证据：架构=$arch，nkvers=$nkvers_line"
}

verify_python() {
    python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"KylinGuard 需要 Python >= 3.10，当前为 {sys.version.split()[0]}")
print(f"[KylinGuard] Python={sys.version.split()[0]}")
PY
}

verify_rust() {
    local current lowest
    current="$(rustc --version | awk '{print $2}')"
    lowest="$(printf '%s\n%s\n' "1.75.0" "$current" | sort -V | head -n 1)"
    [[ "$lowest" == "1.75.0" ]] || \
        die "LoongArch64 源码依赖要求 rustc >= 1.75，当前为 $current"
    log "Rust=$current"
}

require_command sha256sum
require_command find
require_command uname
verify_bundle
verify_target_platform

VERSION="$(tr -d '\r\n' < "$BUNDLE_ROOT/VERSION")"
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$ ]] || \
    die "VERSION 格式不合法"

if ((CHECK_ONLY)); then
    require_command python3
    verify_python
    if command -v dnf >/dev/null 2>&1; then
        log "dnf=$(dnf --version 2>/dev/null | head -n 1)"
    elif ((ALLOW_NON_TARGET == 0)); then
        die "银河麒麟 V11 目标机缺少 dnf"
    else
        log "警告：开发验证主机没有 dnf"
    fi
    require_command systemctl
    log "检查通过：安装包 $VERSION 可进入安装阶段（未修改系统）"
    exit 0
fi

((EUID == 0)) || die "安装阶段必须以 root 运行：sudo ./install.sh"

if ((INSTALL_SYSTEM_DEPS)); then
    require_command dnf
    log "通过当前银河麒麟 repo 安装 Python 与 LoongArch64 源码构建依赖"
    dnf install -y \
        python3 python3-pip python3-devel \
        gcc rust cargo \
        openssl-devel libffi-devel pkgconf \
        sudo shadow-utils coreutils
fi

for command_name in \
    python3 dnf systemctl sudo visudo useradd usermod groupadd getent \
    install sha256sum rustc cargo gcc sort awk; do
    require_command "$command_name"
done
verify_python
verify_rust
[[ -d /run/systemd/system ]] || \
    die "systemd 不是当前系统管理器，无法安装主机服务"
[[ -x /usr/bin/systemctl ]] || die "预期的 /usr/bin/systemctl 不可执行"
[[ -x /bin/bash ]] || die "预期的 /bin/bash 不可执行"

ensure_group() {
    local group="$1"
    if ! getent group "$group" >/dev/null; then
        groupadd --system "$group"
    fi
}

ensure_user() {
    local user="$1" group="$2" home="$3"
    if id "$user" >/dev/null 2>&1; then
        [[ "$(id -u "$user")" != "0" ]] || die "账户 $user 不能是 UID 0"
        [[ "$(id -gn "$user")" == "$group" ]] || \
            die "已有账户 $user 的主组不是 $group，拒绝静默修改"
    else
        useradd --system --gid "$group" --home-dir "$home" \
            --create-home --shell /sbin/nologin "$user"
    fi
}

ensure_group "$CONTROL_GROUP"
ensure_group "$EXEC_GROUP"
ensure_group "$WORKSPACE_GROUP"
ensure_user "$CONTROL_USER" "$CONTROL_GROUP" "$STATE_ROOT"
ensure_user "$EXEC_USER" "$EXEC_GROUP" "$EXEC_HOME"
[[ "$(id -u "$CONTROL_USER")" != "$(id -u "$EXEC_USER")" ]] || \
    die "控制面与执行面账户不能共用 UID"
usermod -a -G "$WORKSPACE_GROUP" "$CONTROL_USER"
usermod -a -G "$WORKSPACE_GROUP" "$EXEC_USER"

install -d -m 0755 -o root -g root "$APP_ROOT" "$APP_ROOT/releases"
install -d -m 0700 -o "$CONTROL_USER" -g "$CONTROL_GROUP" \
    "$STATE_ROOT" "$STATE_ROOT/db" "$STATE_ROOT/secrets" \
    "$STATE_ROOT/secrets/llm" "$STATE_ROOT/secrets/mcp" "$STATE_ROOT/skills"
install -d -m 0750 -o "$EXEC_USER" -g "$EXEC_GROUP" "$EXEC_HOME"
install -d -m 2770 -o "$EXEC_USER" -g "$WORKSPACE_GROUP" "$WORKSPACE_ROOT"
install -d -m 0750 -o root -g "$CONTROL_GROUP" "$CONFIG_ROOT"
install -d -m 0755 -o root -g root "$(dirname -- "$HELPER_PATH")"

RELEASE_ID="$VERSION-$(date -u +%Y%m%d%H%M%S)-$$"
RELEASE_TMP="$(mktemp -d "$APP_ROOT/releases/.install.XXXXXX")"
log "创建隔离 Python 环境；LoongArch64 首次编译 Rust/C 依赖可能需要数分钟"
python3 -m venv "$RELEASE_TMP/venv" || \
    die "python3 -m venv 失败；请确认银河麒麟 python3/pip 组件完整"
"$RELEASE_TMP/venv/bin/python" -m pip install --disable-pip-version-check \
    --no-cache-dir --upgrade 'pip>=25.3,<27' 'setuptools>=68' wheel

WHEEL="$(find "$BUNDLE_ROOT/payload/wheels" -maxdepth 1 -type f \
    -name 'kylinguard-*.whl' -print -quit)"
"$RELEASE_TMP/venv/bin/python" -m pip install --disable-pip-version-check \
    --no-cache-dir \
    --constraint "$BUNDLE_ROOT/deploy/constraints-kylin-v11.txt" \
    --build-constraint "$BUNDLE_ROOT/deploy/constraints-kylin-v11.txt" \
    "$WHEEL"

"$RELEASE_TMP/venv/bin/python" - <<'PY'
from kylinguard.api import create_app
from kylinguard.config import Settings
import fastapi, mcp, openai, pydantic, uvicorn

settings = Settings(_env_file=None, db_path=":memory:", frontend_dist="/nonexistent")
app = create_app(settings, with_tools=False)
assert app.title
print("[KylinGuard] Python 包导入与 FastAPI 工厂检查通过")
PY

install -d -m 0755 "$RELEASE_TMP/frontend" "$RELEASE_TMP/docs"
cp -a -- "$BUNDLE_ROOT/payload/frontend/." "$RELEASE_TMP/frontend/"
cp -a -- "$BUNDLE_ROOT/docs/." "$RELEASE_TMP/docs/"
install -m 0644 "$BUNDLE_ROOT/VERSION" "$RELEASE_TMP/VERSION"
install -m 0644 "$BUNDLE_ROOT/deploy/constraints-kylin-v11.txt" \
    "$RELEASE_TMP/constraints-kylin-v11.txt"
chown -R root:root "$RELEASE_TMP"
# mktemp 创建的发布根默认是 0700；必须显式补上只读/可遍历权限，否则
# systemd 的 kylinguard 和工具侧 kylinguard-exec 都无法进入版本目录。
find "$RELEASE_TMP" -type d -exec chmod 0755 {} +
find "$RELEASE_TMP" -type f -exec chmod go-w {} +
[[ -x "$RELEASE_TMP/venv/bin/python" ]] || die "venv Python 缺少执行权限"
[[ -x "$RELEASE_TMP/venv/bin/uvicorn" ]] || die "uvicorn 缺少执行权限"

RELEASE_PATH="$APP_ROOT/releases/$RELEASE_ID"
mv -- "$RELEASE_TMP" "$RELEASE_PATH"
RELEASE_TMP=""

install -m 0755 -o root -g root \
    "$BUNDLE_ROOT/deploy/kylinguard-privileged" "$HELPER_PATH"
install -m 0640 -o root -g "$CONTROL_GROUP" \
    "$BUNDLE_ROOT/deploy/kylinguard.env" "$CONFIG_ROOT/kylinguard.env"
install -m 0644 -o root -g root \
    "$BUNDLE_ROOT/deploy/kylinguard.service" "$UNIT_PATH"

SUDOERS_CHECK_TMP="$(mktemp /run/kylinguard-sudoers.XXXXXX)"
install -m 0440 -o root -g root \
    "$BUNDLE_ROOT/deploy/sudoers-kylinguard" "$SUDOERS_CHECK_TMP"
visudo -cf "$SUDOERS_CHECK_TMP" >/dev/null || die "sudoers 模板校验失败"
SUDOERS_BACKUP=""
if [[ -f "$SUDOERS_PATH" ]]; then
    SUDOERS_BACKUP="$(mktemp /run/kylinguard-sudoers-backup.XXXXXX)"
    cp -a -- "$SUDOERS_PATH" "$SUDOERS_BACKUP"
fi
install -m 0440 -o root -g root "$SUDOERS_CHECK_TMP" "$SUDOERS_PATH"
if ! visudo -cf /etc/sudoers >/dev/null; then
    if [[ -n "$SUDOERS_BACKUP" ]]; then
        cp -a -- "$SUDOERS_BACKUP" "$SUDOERS_PATH"
    else
        rm -f -- "$SUDOERS_PATH"
    fi
    [[ -z "$SUDOERS_BACKUP" ]] || rm -f -- "$SUDOERS_BACKUP"
    die "安装后的完整 sudoers 校验失败，已回滚"
fi
[[ -z "$SUDOERS_BACKUP" ]] || rm -f -- "$SUDOERS_BACKUP"
rm -f -- "$SUDOERS_CHECK_TMP"
SUDOERS_CHECK_TMP=""

NEW_LINK="$APP_ROOT/.current.$$"
ln -s "releases/$RELEASE_ID" "$NEW_LINK"
mv -Tf -- "$NEW_LINK" "$APP_ROOT/current"

EXPECTED_EXEC_UID="$(id -u "$EXEC_USER")"
ACTUAL_EXEC_UID="$(sudo -u "$CONTROL_USER" -- \
    sudo -n -H -u "$EXEC_USER" -- /usr/bin/id -u)" || \
    die "控制面无法通过 sudo -n 切换到受限执行账户"
[[ "$ACTUAL_EXEC_UID" == "$EXPECTED_EXEC_UID" ]] || die "执行账户 UID 校验失败"
sudo -u "$CONTROL_USER" -- sudo -n -H -u "$EXEC_USER" -- \
    /usr/bin/test -w "$WORKSPACE_ROOT" || die "受限执行账户无法写入工作目录"
if sudo -u "$CONTROL_USER" -- sudo -n -- /usr/bin/true 2>/dev/null; then
    die "安全校验失败：控制面获得了非白名单 root 命令权限"
fi
sudo -u "$EXEC_USER" -- /usr/bin/test ! -r "$STATE_ROOT/db" || \
    die "安全校验失败：执行账户可以读取控制面数据库目录"

systemctl daemon-reload
systemctl enable kylinguard.service
if ((START_SERVICE)); then
    systemctl restart kylinguard.service
    HEALTH_OK=0
    for _ in $(seq 1 30); do
        if "$RELEASE_PATH/venv/bin/python" - <<'PY' >/dev/null 2>&1
import json
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=1) as response:
    assert response.status == 200
    json.load(response)
PY
        then
            HEALTH_OK=1
            break
        fi
        sleep 1
    done
    if ((HEALTH_OK == 0)); then
        systemctl --no-pager -l status kylinguard.service >&2 || true
        journalctl -u kylinguard.service -n 80 --no-pager >&2 || true
        die "服务启动后 30 秒内未通过健康检查"
    fi
fi

log "安装完成：版本=$VERSION，发布目录=$RELEASE_PATH"
if ((START_SERVICE)); then
    log "访问地址：http://127.0.0.1:8000"
else
    log "服务尚未启动；运行 systemctl start kylinguard.service"
fi
log "部署核验：sudo /opt/kylinguard/current/docs/verify-install.sh"
