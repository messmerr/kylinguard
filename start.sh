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
已自动创建 .env。模型提供商与 API Key 可在“模型服务”页面配置。
EOF
fi

echo "正在构建并启动 KylinGuard Docker 容器..."
docker compose up -d --build

echo
echo "启动完成。"
echo "- 访问地址: http://127.0.0.1:8000"
echo "- 查看状态: docker compose ps"
echo "- 查看日志: docker compose logs -f"
echo "- 停止服务: docker compose down"
