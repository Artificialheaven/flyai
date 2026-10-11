/* 调 android 平台 Gradle 打 debug APK：android/app/build-outputs/apk/debug/app-debug.apk
 * 若全局 ~/.gradle/gradle.properties 里配了本地代理（127.0.0.1:xxxx）而代理没开，
 * wrapper 下载与 daemon 依赖下载都会 Connection refused。打期间临时注释掉，结束恢复。
 */
"use strict";

const net = require("net");
const os = require("os");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const propsPath = path.join(os.homedir(), ".gradle", "gradle.properties");
const bakPath = propsPath + ".flyai-bak";

function portOpen(port, host) {
  return new Promise(function (resolve) {
    const s = net.createConnection({ port: port, host: host });
    let done = false;
    function finish(v) { if (!done) { done = true; try { s.destroy(); } catch (e) {} resolve(v); } }
    s.once("connect", function () { finish(true); });
    s.once("error", function () { finish(false); });
    setTimeout(function () { finish(false); }, 1500);
  });
}

/* 收集 gradle.properties 中代理配置：行 -> {host, port}；返回需要注释的行号 */
async function staleProxyLines(text) {
  const lines = text.split(/\r?\n/);
  const ports = [];
  lines.forEach(function (ln) {
    const m = /^\s*systemProp\.(?:https?|http)\.proxyHost\s*=\s*(\S+)/.exec(ln);
    if (m) {
      const pm = /^\s*systemProp\.(?:https?|http)\.proxyPort\s*=\s*(\d+)/;
      let port = 0;
      for (const l2 of lines) { const mm = pm.exec(l2); if (mm) { port = parseInt(mm[1], 10); break; } }
      ports.push({ host: m[1], port: port });
    }
  });
  const bad = [];
  for (const p of ports) {
    if (!p.port || !(await portOpen(p.port, p.host))) bad.push(p);
  }
  return bad;
}

function applyPropsBackup(text, stale) {
  fs.writeFileSync(bakPath, text, "utf-8");
  const lines = text.split(/\r?\n/);
  const out = lines.map(function (ln) {
    const host = /^\s*systemProp\..*\.proxyHost\s*=/.test(ln);
    const port = /^\s*systemProp\..*\.proxyPort\s*=/.test(ln);
    if (host || port) {
      const m = /^\s*systemProp\.(https?|http)\./.exec(ln);
      const pt = m ? m[1] : "http";
      const isStale = stale.some(function (p) {
        return ln.indexOf(p.host) >= 0 || (p.port && ln.indexOf(String(p.port)) >= 0);
      });
      if (isStale) return "# [flyai 临时停用, 构建后恢复] " + ln;
    }
    return ln;
  });
  fs.writeFileSync(propsPath, out.join(os.EOL), "utf-8");
}

function restoreProps() {
  if (fs.existsSync(bakPath)) {
    fs.copyFileSync(bakPath, propsPath);
    fs.unlinkSync(bakPath);
    console.log("gradle.properties 已恢复");
  }
}

async function main() {
  let neutralized = false;
  if (fs.existsSync(propsPath)) {
    if (fs.existsSync(bakPath)) restoreProps();  // 上次异常中断留下的备份
    const text = fs.readFileSync(propsPath, "utf-8");
    const stale = await staleProxyLines(text);
    if (stale.length) {
      console.log("检测到未运行的本地代理", stale, "→ 构建期间临时停用");
      applyPropsBackup(text, stale);
      neutralized = true;
    }
  }

  const gradlew = path.resolve(
    __dirname, "..", "android",
    process.platform === "win32" ? "gradlew.bat" : "gradlew"
  );
  // 默认 assembleDebug；build:release 脚本传入 assembleRelease
  const task = process.argv[2] || "assembleDebug";

  // 固定 JDK：优先现有 JAVA_HOME（须有 bin/java），否则回退 Android Studio 自带 jbr。
  // 不固定时 PATH 上若有过新的 JDK（如 Java 25），Gradle 8.11 会报
  // "Unsupported class file major version"。
  function hasJava(home) {
    return home && fs.existsSync(path.join(
      home, "bin", process.platform === "win32" ? "java.exe" : "java"));
  }
  const jbrCandidates = [
    "D:/Program/AndroidStudio/jbr",
    "C:/Program Files/Android/Android Studio/jbr",
  ];
  let javaHome = hasJava(process.env.JAVA_HOME) ? process.env.JAVA_HOME : null;
  if (!javaHome) {
    for (const c of jbrCandidates) {
      if (hasJava(c)) { javaHome = c; break; }
    }
  }
  // 固定 Android SDK：优先现有 ANDROID_SDK_HOME/ANDROID_HOME，否则回退用户
  // 目录下的 Android/Sdk。不固定时 Gradle 找不到 SDK（local.properties 不入库）。
  const sdkCandidates = [
    process.env.ANDROID_SDK_ROOT,
    process.env.ANDROID_HOME,
    path.join(os.homedir(), "AppData", "Local", "Android", "Sdk"),
    "C:/Android/Sdk",
  ];
  let sdkHome = null;
  for (const c of sdkCandidates) {
    if (c && fs.existsSync(path.join(c, "platforms"))) { sdkHome = c; break; }
  }

  const buildEnv = Object.assign(
    {}, process.env,
    javaHome ? { JAVA_HOME: javaHome } : {},
    sdkHome ? { ANDROID_SDK_ROOT: sdkHome, ANDROID_HOME: sdkHome } : {}
  );
  if (javaHome) console.log("使用 JDK:", javaHome);
  if (sdkHome) console.log("使用 Android SDK:", sdkHome);

  const r = spawnSync(gradlew, [task], {
    cwd: path.resolve(__dirname, "..", "android"),
    stdio: "inherit",
    env: buildEnv,
    // Windows：.bat 必须经 cmd.exe（新版 Node 直接 spawn .bat 会 EINVAL）
    shell: process.platform === "win32",
  });
  if (r.error) console.error(r.error.message);

  if (neutralized) restoreProps();
  process.exit(r.status == null ? 1 : r.status);
}

process.on("SIGINT", function () { restoreProps(); process.exit(130); });
process.on("SIGTERM", function () { restoreProps(); process.exit(143); });

main();
