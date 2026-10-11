/* 把后端静态站打进 www/：
 *   www/index.html、www/login.html（外壳页放在根，对应服务器的 / 与 /login）
 *   www/static/...（其余静态资源，对应服务器 /static 挂载）
 * 服务器写死在前端（app.js APP_SERVER / login.html），此处只做拷贝。
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
fs.copyFileSync(path.join(SRC, "login.html"), path.join(DEST, "login.html"));

// 本地联调：FLYAI_APP_SERVER=http://10.0.2.2:8000 时只替换拷贝里的写死域名，
// 不动源码；默认（不设）仍为 https://fly.eastcloud.top
const override = process.env.FLYAI_APP_SERVER;
if (override) {
  const DOMAIN = "https://fly.eastcloud.top";
  let total = 0;
  function walk(dir) {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name);
      const st = fs.statSync(p);
      if (st.isDirectory()) { walk(p); continue; }
      if (!/\.(js|html)$/.test(name)) continue;
      const s = fs.readFileSync(p, "utf-8");
      const n = s.split(DOMAIN).length - 1;
      if (n) { fs.writeFileSync(p, s.split(DOMAIN).join(override), "utf-8"); total += n; }
    }
  }
  walk(DEST);
  console.log("FLYAI_APP_SERVER=" + override + "（替换 " + total + " 处，仅拷贝）");
}
console.log("web assets copied to", DEST);
