#!/usr/bin/env node
/**
 * 把 webapp/dist 的构建产物同步到 flyai/web/static（FastAPI 由 / 与 /static 直接提供）。
 * 目标目录整体替换：旧的 web 前端文件不再保留（回退靠 Git 历史），
 * 避免新旧资源混放导致 index.html 与静态目录不一致。
 */
import { cpSync, existsSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(fileURLToPath(import.meta.url))
const dist = path.join(root, 'dist')
const dest = path.join(root, '..', 'flyai', 'web', 'static')

if (!existsSync(path.join(dist, 'index.html'))) {
  console.error(`构建产物缺失：${dist}（先运行 npm run build）`)
  process.exit(1)
}

rmSync(dest, { recursive: true, force: true })
cpSync(dist, dest, { recursive: true })
console.log(`已同步 ${dist} → ${dest}`)