#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

PURGE=0
YES=0

usage() {
    cat <<'EOF'
用法：sudo ./uninstall.sh [--purge --yes]

默认删除服务、程序、helper 和 sudoers，但保留 /etc/kylinguard、
/var/lib/kylinguard 与 /srv/kylinguard-workspace。

--purge --yes 会不可恢复地删除配置、数据库、模型凭据、工作目录和专用账户。
EOF
}

die() {
    printf '[KylinGuard] ERROR: %s\n' "$*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --purge) PURGE=1 ;;
        --yes) YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
    shift
done

((EUID == 0)) || die "必须以 root 运行"
if ((PURGE && !YES)); then
    die "--purge 会删除数据库、凭据和工作目录，必须同时显式传入 --yes"
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now kylinguard.service 2>/dev/null || true
fi

rm -f -- \
    /etc/systemd/system/kylinguard.service \
    /etc/sudoers.d/kylinguard \
    /usr/local/libexec/kylinguard/kylinguard-privileged

safe_remove_tree() {
    case "$1" in
        /opt/kylinguard|/etc/kylinguard|/var/lib/kylinguard|\
        /var/lib/kylinguard-exec|/srv/kylinguard-workspace)
            rm -rf -- "$1"
            ;;
        *) die "拒绝删除非固定目录：$1" ;;
    esac
}

safe_remove_tree /opt/kylinguard

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl reset-failed kylinguard.service 2>/dev/null || true
fi

if ((PURGE)); then
    safe_remove_tree /etc/kylinguard
    safe_remove_tree /var/lib/kylinguard
    safe_remove_tree /var/lib/kylinguard-exec
    safe_remove_tree /srv/kylinguard-workspace

    id kylinguard-exec >/dev/null 2>&1 && userdel kylinguard-exec || true
    id kylinguard >/dev/null 2>&1 && userdel kylinguard || true
    getent group kylinguard-workspace >/dev/null 2>&1 && \
        groupdel kylinguard-workspace || true
    getent group kylinguard-exec >/dev/null 2>&1 && groupdel kylinguard-exec || true
    getent group kylinguard >/dev/null 2>&1 && groupdel kylinguard || true
    printf '[KylinGuard] 已彻底卸载；数据、凭据与工作目录不可恢复。\n'
else
    printf '%s\n' \
        '[KylinGuard] 程序已卸载，数据与账户已保留。' \
        '[KylinGuard] 如确认不再需要，使用：sudo ./uninstall.sh --purge --yes'
fi
