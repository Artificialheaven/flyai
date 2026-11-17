/* 把后端静态站打进 www/：
 *   www/index.html（新版 SPA 单入口，对应服务器的 /）
 *   www/static/...（其余静态资源，对应服务器 /static 挂载）
 * 新版已取消独立 login.html；若源目录仍有（旧站回退场景）则一并拷贝。
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = path.resolve(__dirname, "..", "..", "flyai", "web", "static");
const DEST = path.resolve(__dirname, "..", "www");

function rmrf(p) {
  if (!fs.existsSync(p)) return;
  fs.rmSync(p, { recursive: true, force: true });
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const name of fs.readdirSync(src)) {
    const s = path.join(src, name);
    const d = path.join(dest, name);
    const st = fs.statSync(s);
    if (st.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

rmrf(DEST);
fs.mkdirSync(DEST, { recursive: true });
copyDir(SRC, path.join(DEST, "static"));
fs.copyFileSync(path.join(SRC, "index.html"), path.join(DEST, "index.html"));
const loginSrc = path.join(SRC, "login.html");
// 旧 APK/书签仍可能访问 login.html：新站没有该文件时用 SPA 入口顶上
fs.copyFileSync(
  fs.existsSync(loginSrc) ? loginSrc : path.join(SRC, "index.html"),
  path.join(DEST, "login.html")
);
// SPA 从页面根目录取图标（Web 端由 FastAPI 路由提供同名文件）
const faviconSrc = path.join(SRC, "favicon.svg");
if (fs.existsSync(faviconSrc)) fs.copyFileSync(faviconSrc, path.join(DEST, "favicon.svg"));

// APK 内页面源是 https://localhost，相对路径请求不会到达后端，
// 因此 config.json 必须写死绝对后端地址（Web 站点的同名文件仍是同源空串）。
// 本地联调：FLYAI_APP_SERVER=http://10.0.2.2:8000 覆盖，默认线上 https://fly.eastcloud.top
const DEFAULT_SERVER = "https://fly.eastcloud.top";
const override = (process.env.FLYAI_APP_SERVER || "").trim();
const apiBase = override || DEFAULT_SERVER;
let baseConfig = {};
try {
  baseConfig = JSON.parse(fs.readFileSync(path.join(SRC, "config.json"), "utf-8"));
} catch {
  // 源目录缺 config.json 时用内置默认结构
}
fs.writeFileSync(
  path.join(DEST, "config.json"),
  JSON.stringify(Object.assign({}, baseConfig, { apiBase }), null, 2) + "\n",
  "utf-8"
);
console.log("APK apiBase = " + apiBase + (override ? "（FLYAI_APP_SERVER 覆盖）" : ""));
console.log("web assets copied to", DEST);
