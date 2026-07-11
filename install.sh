#!/usr/bin/env bash
set -euo pipefail

APP_USER="${KG_APP_USER:-kylinguard}"
EXEC_USER="${KG_EXEC_USER:-kylinguard-exec}"
INSTALL_DIR="${KG_INSTALL_DIR:-/opt/kylinguard}"
STATE_DIR="${KG_STATE_DIR:-/var/lib/kylinguard}"
CONFIG_DIR="${KG_CONFIG_DIR:-/etc/kylinguard}"
HELPER_DIR="/usr/local/libexec/kylinguard"
WORKSPACE_DIR="${KG_WORKSPACE_DIR:-/srv/kylinguard-workspaces/default}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 运行：sudo ./install.sh" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少 python3，请先从系统软件源安装。" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "缺少 npm，请先从系统软件源安装 nodejs/npm。" >&2
  exit 1
fi

id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --home-dir "/var/lib/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
id -u "$EXEC_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --home-dir "/var/lib/$EXEC_USER" --shell /usr/sbin/nologin "$EXEC_USER"

install -d -m 0755 "$INSTALL_DIR" "$HELPER_DIR"
install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$CONFIG_DIR"
install -d -m 0750 -o "$EXEC_USER" -g "$EXEC_USER" "$WORKSPACE_DIR"

rsync -a --delete \
  --exclude .git \
  --exclude .venv \
  --exclude node_modules \
  --exclude frontend/node_modules \
  --exclude frontend/dist \
  --exclude backend/data \
  --exclude docs/superpowers \
  --exclude .env \
  ./ "$INSTALL_DIR/"

if [ ! -f "$CONFIG_DIR/kylinguard.env" ]; then
  install -m 0640 -o root -g "$APP_USER" .env.example "$CONFIG_DIR/kylinguard.env"
  cat >> "$CONFIG_DIR/kylinguard.env" <<EOF
KG_DB_PATH=$STATE_DIR/kylinguard.db
KG_LLM_SECRETS_DIR=$STATE_DIR/provider-secrets
KG_EXEC_USER=$EXEC_USER
KG_PRIVILEGED_HELPER=$HELPER_DIR/execctl
EOF
  echo "已创建 $CONFIG_DIR/kylinguard.env，请至少设置真实 KG_ADMIN_PASSWORD。"
fi

if ! grep -q '^KG_WORKSPACE_ROOT=' "$CONFIG_DIR/kylinguard.env"; then
  echo "KG_WORKSPACE_ROOT=$WORKSPACE_DIR" >> "$CONFIG_DIR/kylinguard.env"
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR/backend"

(cd "$INSTALL_DIR/frontend" && npm install && npm run build)

install -m 0750 -o root -g root deploy/execctl "$HELPER_DIR/execctl"
install -m 0640 -o root -g root deploy/sudoers-kylinguard /etc/sudoers.d/kylinguard
visudo -cf /etc/sudoers.d/kylinguard

install -m 0644 deploy/kylinguard.service /etc/systemd/system/kylinguard.service
systemctl daemon-reload
systemctl enable kylinguard.service

cat <<EOF
安装完成。

下一步：
1. 编辑 $CONFIG_DIR/kylinguard.env
2. 执行 systemctl start kylinguard
3. 本机浏览器访问 http://127.0.0.1:8000；远程访问请使用 SSH 端口转发，
   或配置同机 HTTPS 反向代理，切勿在局域网明文暴露 8000 端口
EOF
