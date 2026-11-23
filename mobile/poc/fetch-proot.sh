#!/usr/bin/env bash
# 下载 Termux 的 PRoot 与依赖（Android/bionic），解包到 $1/proot-pkg
set -euo pipefail
WORK="${1:-/workspace/poc}"
BASE=https://packages.termux.dev/apt/termux-main
mkdir -p "$WORK/proot-pkg" && cd "$WORK"
for f in \
  pool/main/p/proot/proot_5.1.107.102_aarch64.deb \
  pool/main/libt/libtalloc/libtalloc_2.5.0_aarch64.deb \
  pool/main/liba/libandroid-shmem/libandroid-shmem_0.7_aarch64.deb; do
  curl -fSL -O "$BASE/$f"
done
for d in proot_*.deb libtalloc_*.deb libandroid-shmem_*.deb; do
  dpkg-deb -x "$d" proot-pkg
done
echo "PRoot 已解包到 $WORK/proot-pkg"
