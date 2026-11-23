#!/bin/sh
# arm64 构建机运行 x86_64 aapt2：qemu + x86_64 基础 rootfs。
# 期望目录：$POC_DIR/qemu-x86_64、$POC_DIR/x86root、Android SDK build-tools/35.0.0/aapt2
POC_DIR="${POC_DIR:-/workspace/poc}"
SDK="${ANDROID_HOME:-/workspace/android-sdk}"
exec "$POC_DIR/qemu-x86_64" -L "$POC_DIR/x86root" "$SDK/build-tools/35.0.0/aapt2" "$@"
