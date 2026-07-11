#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "未检测到 docker，请先安装 Docker。" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "未检测到 docker compose 插件，请先安装 Docker Compose。" >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  cat <<'EOF'
已自动创建 .env，请至少补充以下配置后重新执行：
- KG_LLM_API_KEY
- KG_ADMIN_PASSWORD
EOF
  exit 1
fi

if grep -q '^KG_LLM_API_KEY=sk-你的密钥$' .env 2>/dev/null; then
  echo "检测到 .env 仍在使用示例 KG_LLM_API_KEY，请先改成真实密钥。" >&2
  exit 1
fi

if grep -Eq '^KG_ADMIN_PASSWORD=(|请设置强密码|change-me|changeme|password)$' .env 2>/dev/null; then
  echo "检测到 .env 尚未设置安全的 KG_ADMIN_PASSWORD，请先改成你的登录密码。" >&2
  exit 1
fi

echo "正在构建并启动 KylinGuard Docker 容器..."
docker compose up -d --build

echo
echo "启动完成。"
echo "- 访问地址: http://127.0.0.1:8000"
echo "- 查看状态: docker compose ps"
echo "- 查看日志: docker compose logs -f"
echo "- 停止服务: docker compose down"
