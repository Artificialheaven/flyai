#!/usr/bin/env bash
# 生成 FlyAI release 签名密钥（只需执行一次；密钥务必备份，丢了无法给老用户出更新）
# 密码不写入仓库：执行前设置环境变量 FLYAI_KEYSTORE_PASS。
set -e

KEYTOOL="D:/Program/AndroidStudio/jbr/bin/keytool"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
JKS="$DIR/android/flyai-release.jks"

if [ -f "$JKS" ]; then
  echo "密钥已存在：$JKS（不再重复生成）"
  exit 0
fi

: "${FLYAI_KEYSTORE_PASS:?请先设置环境变量 FLYAI_KEYSTORE_PASS（签名密钥库密码）}"

"$KEYTOOL" -genkeypair -v \
  -keystore "$JKS" \
  -alias flyai \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass "$FLYAI_KEYSTORE_PASS" \
  -keypass "$FLYAI_KEYSTORE_PASS" \
  -dname 'CN=FlyAI, OU=Mobile, O=eastcloud.top, C=CN'

echo "OK: $JKS"
echo "请把同一密码写入 mobile/android/keystore.properties（已被 .gitignore 忽略）"
