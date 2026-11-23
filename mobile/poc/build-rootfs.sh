#!/usr/bin/env bash
# 生成 FlyAI 本地一体化 APK 所需的 Debian/Ubuntu 用户态 rootfs。
# 仅支持 aarch64 Linux 构建机（目标设备同为 arm64），需要 root（chroot）。
set -euo pipefail

WORK="${1:-/workspace/poc}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOTFS="$WORK/rootfs"
BASE_URL="https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-arm64.tar.gz"

mkdir -p "$WORK"
if [ ! -f "$WORK/ubuntu-base.tar.gz" ]; then
  echo "== 下载 Ubuntu base (arm64)"
  curl -fSL --retry 3 -o "$WORK/ubuntu-base.tar.gz" "$BASE_URL"
fi

echo "== 解包 base rootfs"
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
tar xzf "$WORK/ubuntu-base.tar.gz" -C "$ROOTFS"
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
cat > "$ROOTFS/etc/apt/sources.list" <<'EOF'
deb http://mirrors.aliyun.com/ubuntu-ports noble main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu-ports noble-updates main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu-ports noble-security main restricted universe multiverse
EOF

echo "== 安装 Python 运行时"
chroot "$ROOTFS" /bin/bash -c \
  "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip ca-certificates tzdata"

echo "== 复制 FlyAI 源码"
mkdir -p "$ROOTFS/opt/flyai"
tar cf - -C "$REPO_ROOT" \
  --exclude=.git --exclude=.venv --exclude=data --exclude=show \
  --exclude=webapp/node_modules --exclude=webapp/dist --exclude=node_modules \
  --exclude=mobile/android --exclude=__pycache__ --exclude=.pytest_cache . \
  | tar xf - -C "$ROOTFS/opt/flyai"

echo "== 安装 Python 依赖"
chroot "$ROOTFS" /bin/bash -c \
  "cd /opt/flyai && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt"

echo "== 本地监听配置"
python3 - "$ROOTFS/opt/flyai/config/config.json" <<'PY'
import json, sys
src = sys.argv[1].replace('config.json', 'config.example.json')
data = json.load(open(src))
data.setdefault('server', {})['host'] = '127.0.0.1'
data['server']['port'] = 8000
json.dump(data, open(sys.argv[1], 'w'), ensure_ascii=False, indent=2)
PY

cat > "$ROOTFS/opt/flyai/start.sh" <<'EOF'
#!/bin/sh
cd /opt/flyai || exit 1
export HOME=/root
export PYTHONUNBUFFERED=1
exec .venv/bin/python main.py
EOF
chmod +x "$ROOTFS/opt/flyai/start.sh"

echo "== 精简 rootfs"
cd "$ROOTFS"
rm -rf tests docs webapp mobile show promo deploy example scripts .pytest_cache \
       var/lib/apt/lists/* var/cache/apt/archives/* var/cache/apt/*.bin \
       usr/share/doc/* usr/share/man/* usr/share/locale/* usr/share/info/* \
       usr/libexec/gcc usr/lib/gcc usr/include usr/lib/python3.12/config-* \
       usr/lib/aarch64-linux-gnu/perl usr/lib/aarch64-linux-gnu/perl-base \
       usr/lib/aarch64-linux-gnu/libasan* usr/lib/aarch64-linux-gnu/libtsan* \
       usr/lib/aarch64-linux-gnu/libhwasan* usr/lib/aarch64-linux-gnu/libubsan* \
       usr/bin/perl* usr/bin/gcc* usr/bin/g++* 2>/dev/null || true
find usr/lib/python3.12 -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

echo "== 打包"
tar czf "$WORK/rootfs.tar.gz" --numeric-owner -C "$ROOTFS" .
ls -lh "$WORK/rootfs.tar.gz"
