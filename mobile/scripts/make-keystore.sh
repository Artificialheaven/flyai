#!/usr/bin/env bash
# 生成 FlyAI release 签名密钥（只需执行一次；密钥务必备份，丢了无法给老用户出更新）
set -e

KEYTOOL="D:/Program/AndroidStudio/jbr/bin/keytool"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
JKS="$DIR/android/flyai-release.jks"

if [ -f "$JKS" ]; then
  echo "密钥已存在：$JKS（不再重复生成）"
  exit 0
fi

"$KEYTOOL" -genkeypair -v \
  -keystore "$JKS" \
  -alias flyai \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass '__REMOVED_KEYSTORE_PASSWORD__' \
  -keypass '__REMOVED_KEYSTORE_PASSWORD__' \
  -dname 'CN=FlyAI, OU=Mobile, O=eastcloud.top, C=CN'

echo "OK: $JKS"
