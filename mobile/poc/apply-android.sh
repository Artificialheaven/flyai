#!/usr/bin/env bash
# 把 POC 补丁应用到 `npx cap add android` 生成的工程，并放入 rootfs/proot 资源。
# 用法：mobile/poc/apply-android.sh [POC_DIR]   默认 POC_DIR=/workspace/poc
set -euo pipefail

POC_DIR="${1:-/workspace/poc}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_DIR="$(cd "$HERE/.." && pwd)/android"
APP="$ANDROID_DIR/app"

[ -d "$APP" ] || { echo "先执行 npx cap add android"; exit 1; }
[ -f "$POC_DIR/rootfs.tar.gz" ] || { echo "缺少 $POC_DIR/rootfs.tar.gz，先跑 build-rootfs.sh"; exit 1; }

echo "== Java 源码"
mkdir -p "$APP/src/main/java/top/eastcloud/flyai/poc"
cp "$HERE/android/"*.java "$APP/src/main/java/top/eastcloud/flyai/poc/"

echo "== 资源"
mkdir -p "$APP/src/main/assets/proot"
cp "$POC_DIR/rootfs.tar.gz" "$APP/src/main/assets/rootfs_payload.bin"
cp "$POC_DIR/proot-pkg/data/data/com.termux/files/usr/bin/proot" "$APP/src/main/assets/proot/proot"
cp "$POC_DIR/proot-pkg/data/data/com.termux/files/usr/libexec/proot/loader" "$APP/src/main/assets/proot/loader"
cp "$POC_DIR/proot-pkg/data/data/com.termux/files/usr/lib/libtalloc.so.2.5.0" "$APP/src/main/assets/proot/libtalloc.so.2"
cp "$POC_DIR/proot-pkg/data/data/com.termux/files/usr/lib/libandroid-shmem.so" "$APP/src/main/assets/proot/libandroid-shmem.so"

echo "== Manifest"
python3 - "$APP/src/main/AndroidManifest.xml" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace('<application\n        android:allowBackup="true"',
              '<application\n        android:allowBackup="true"\n        android:usesCleartextTraffic="true"')
if 'BootService' not in s:
    s = s.replace('        <provider', '''        <service
            android:name=".BootService"
            android:exported="false"
            android:foregroundServiceType="dataSync" />

        <provider''')
if 'FOREGROUND_SERVICE' not in s:
    s = s.replace('    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />', '''    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_DATA_SYNC" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />''')
open(p, 'w').write(s)
PY

echo "== Gradle"
python3 - "$APP/build.gradle" <<'PY'
import os
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace('targetSdkVersion rootProject.ext.targetSdkVersion', 'targetSdkVersion 28')
if 'poc-release.jks' not in s:
    # 签名口令从 mobile/keystore.properties 读取，不写死进仓库
    props = {}
    props_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'keystore.properties')
    if os.path.exists(props_path):
        for line in open(props_path, encoding='utf-8'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                props[k.strip()] = v.strip()
    store_pw = props.get('storePassword', os.environ.get('FLYAI_STORE_PASSWORD', 'changeme'))
    key_pw = props.get('keyPassword', os.environ.get('FLYAI_KEY_PASSWORD', store_pw))
    key_alias = props.get('keyAlias', 'flyai')
    s = s.replace('''    buildTypes {''', f'''    lint {{
        checkReleaseBuilds false
        abortOnError false
    }}
    signingConfigs {{
        release {{
            storeFile file('../../poc-release.jks')
            storePassword '{store_pw}'
            keyAlias '{key_alias}'
            keyPassword '{key_pw}'
        }}
    }}
    buildTypes {{''')
    s = s.replace('''        release {
            minifyEnabled false''', '''        release {
            signingConfig signingConfigs.release
            minifyEnabled false''')
open(p, 'w').write(s)
PY

if [ -n "${AAPT2_OVERRIDE:-}" ]; then
  grep -q aapt2FromMavenOverride "$ANDROID_DIR/gradle.properties" 2>/dev/null \
    || echo "android.aapt2FromMavenOverride=$AAPT2_OVERRIDE" >> "$ANDROID_DIR/gradle.properties"
fi

if [ ! -f "$ANDROID_DIR/../poc-release.jks" ]; then
  echo "缺少签名文件：mobile/poc-release.jks，可执行："
  echo "  keytool -genkeypair -keystore mobile/poc-release.jks -alias flyai -storepass flyaipoc -keypass flyaipoc -dname 'CN=FlyAI POC' -keyalg RSA -keysize 2048 -validity 10000"
fi

echo "完成。构建：cd mobile/android && ./gradlew assembleRelease"
