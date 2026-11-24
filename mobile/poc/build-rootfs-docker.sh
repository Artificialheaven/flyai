#!/usr/bin/env bash
# x86_64/任意架构构建机生成 arm64 rootfs（Docker + QEMU binfmt）。
# 用法：scripts/build-rootfs-docker.sh [WORK_DIR] [REPO_ROOT]
set -euo pipefail

WORK="${1:-$PWD/poc}"
REPO_ROOT="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGE="${FLYAI_BASE_IMAGE:-ubuntu:24.04}"
CID=""

cleanup() {
  [ -n "$CID" ] && docker rm -f "$CID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

command -v docker >/dev/null || { echo "需要 Docker"; exit 1; }
mkdir -p "$WORK"

# Linux 主机需要 arm64 binfmt；Docker Desktop 已内置模拟，可忽略失败
docker run --privileged --rm tonistiigi/binfmt --install arm64 >/dev/null 2>&1 || true

echo "== 创建 arm64 容器"
CID=$(docker create --platform linux/arm64 "$IMAGE" sleep infinity)
docker start "$CID" >/dev/null

echo "== 复制源码"
docker cp "$REPO_ROOT" "$CID:/opt/flyai"
docker exec "$CID" bash -c "rm -rf /opt/flyai/.git /opt/flyai/data /opt/flyai/show /opt/flyai/.venv \
  /opt/flyai/webapp/node_modules /opt/flyai/webapp/dist /opt/flyai/mobile/android"

echo "== 安装 Python 与依赖（QEMU 模拟，首次约 10-20 分钟）"
docker exec "$CID" bash -c "export DEBIAN_FRONTEND=noninteractive; \
  apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip ca-certificates tzdata && \
  cd /opt/flyai && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && \
  .venv/bin/pip install -q -r requirements.txt"

echo "== 本地监听配置"
docker exec "$CID" bash -c "cd /opt/flyai && python3 - <<'PY'
import json
d = json.load(open('config/config.example.json'))
d.setdefault('server', {})['host'] = '127.0.0.1'
d['server']['port'] = 8000
json.dump(d, open('config/config.json', 'w'), ensure_ascii=False, indent=2)
PY
cat > start.sh <<'SH'
#!/bin/sh
cd /opt/flyai || exit 1
export HOME=/root
export PYTHONUNBUFFERED=1
exec .venv/bin/python main.py
SH
chmod +x start.sh"

echo "== 清理体积"
docker exec "$CID" bash -c "cd /opt/flyai && rm -rf tests docs webapp mobile show promo deploy example scripts \
  .pytest_cache requirements-dev.txt requirements-browser.txt README.md && \
  rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /usr/share/doc/* /usr/share/man/* \
  /usr/share/locale/* /usr/libexec/gcc /usr/lib/gcc /usr/include && \
  find usr/lib/python3.12 -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true"

echo "== 导出 rootfs"
docker export "$CID" | gzip -6 > "$WORK/rootfs.tar.gz"
ls -lh "$WORK/rootfs.tar.gz"
echo "完成：$WORK/rootfs.tar.gz"
