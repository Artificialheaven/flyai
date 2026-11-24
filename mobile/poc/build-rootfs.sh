#!/usr/bin/env bash
# 生成 FlyAI 本地一体化 APK 所需的 Debian/Ubuntu 用户态 rootfs。
# 仅支持 aarch64 Linux 构建机（目标设备同为 arm64），需要 root（chroot）。
set -euo pipefail

WORK="${1:-/workspace/poc}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOTFS="$WORK/rootfs"
BASE_URL="https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-arm64.tar.gz"
# 允许 CI / 海外构建机切换到官方 ports 源
APT_MIRROR="${POC_APT_MIRROR:-http://mirrors.aliyun.com/ubuntu-ports}"

mkdir -p "$WORK"
if [ ! -f "$WORK/ubuntu-base.tar.gz" ]; then
  echo "== 下载 Ubuntu base (arm64)"
  curl -fSL --retry 3 -o "$WORK/ubuntu-base.tar.gz" "$BASE_URL"
fi

echo "== 解包 base rootfs"
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
tar xzf "$WORK/ubuntu-base.tar.gz" -C "$ROOTFS"
# Ubuntu base 包里没有 /dev 节点，dpkg 维护脚本常重定向到 /dev/null，
# 缺失会让 apt 安装以 100 退出。chroot 与宿主共享网络/设备命名空间。
mkdir -p "$ROOTFS/dev"
for spec in "null c 1 3" "zero c 1 5" "random c 1 8" "urandom c 1 9" "tty c 5 0"; do
  set -- $spec
  [ -e "$ROOTFS/dev/$1" ] || { mknod -m 666 "$ROOTFS/dev/$1" "$2" "$3" || true; }
done
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
# chroot 共享网络命名空间，systemd-resolved 的 127.0.0.53 通常可达；
# 保留它，再追加公网 DNS 兜底（部分 CI 无法直连 127.0.0.53）。
if grep -q "127.0.0.53" "$ROOTFS/etc/resolv.conf" 2>/dev/null; then
  printf "nameserver 127.0.0.53\nnameserver 223.5.5.5\nnameserver 8.8.8.8\n" \
    > "$ROOTFS/etc/resolv.conf"
fi
cat > "$ROOTFS/etc/apt/sources.list" <<EOF
deb ${APT_MIRROR} noble main restricted universe multiverse
deb ${APT_MIRROR} noble-updates main restricted universe multiverse
deb ${APT_MIRROR} noble-security main restricted universe multiverse
EOF

echo "== chroot 自检"
chroot "$ROOTFS" /bin/bash -c '
  echo "uname: $(uname -m 2>/dev/null || echo unknown)"
  echo "/dev/null: $(ls -l /dev/null 2>&1 || echo missing)"
  echo "resolv:"; cat /etc/resolv.conf
  echo "dns: $(getent hosts ports.ubuntu.com 2>&1 || echo FAIL)"
' || echo "chroot 自检失败（继续尝试 apt）"

echo "== 安装 Python 运行时"
chroot "$ROOTFS" /bin/bash -c \
  "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip ca-certificates tzdata"

echo "== 复制 FlyAI 源码"
mkdir -p "$ROOTFS/opt/flyai"
tar cf - -C "$REPO_ROOT" \
  --exclude=.git --exclude=.claude --exclude=.venv --exclude=data --exclude=show \
  --exclude=test --exclude=workspace \
  --exclude=.poc-cache --exclude=.poc-build --exclude=release --exclude=.github \
  --exclude=webapp/node_modules --exclude=webapp/dist --exclude=node_modules \
  --exclude=mobile/android --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude='*.jks' --exclude='*.keystore' --exclude=mobile/keystore.properties \
  --exclude=config/config.json --exclude='config/config.json.bak-*' \
  --exclude=tools/frp --exclude=flyai/web/static/poc --exclude='tmp_*' . \
  | tar xf - -C "$ROOTFS/opt/flyai"

echo "== 安装 Python 依赖"
chroot "$ROOTFS" /bin/bash -c \
  "cd /opt/flyai && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt"

# 不打包 config/config.json：首次启动由 load_config 从 example 生成，
# 避免升级解包时覆盖设备上的管理员账号等用户配置。

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
