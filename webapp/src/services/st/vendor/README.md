# 内置第三方前端库

卡内整页应用常从 CDN 加载 jQuery / lodash；断网或屏蔽 CDN 时状态栏脚本会初始化
失败。这里保存一份官方构建，由 `services/st/cardApp.ts` 在卡片引用时以内联
`<script>` 注入沙箱文档，不再发起网络请求。酒馆主界面本来就带 lodash 全局 `_`，
大量卡内应用直接用它读 `stat_data`。

| 文件 | 版本 | 来源 | 许可 |
|---|---|---|---|
| `jquery-3.6.0.min.js` | 3.6.0 | `https://code.jquery.com/jquery-3.6.0.min.js` | MIT（文件头保留版权声明） |
| `lodash-4.18.1.min.js` | 4.18.1 | npm `lodash-es`，经 `esbuild --bundle --format=iife --global-name=__lodashVendor --minify` 打成全局 | MIT（`lodash-LICENSE.txt`） |
| `zod-4.6.7.min.js` | 4.6.7 | npm `zod`，经 `esbuild --bundle --format=iife --global-name=z --platform=browser --minify` 打成全局 | MIT（`zod-LICENSE.txt`） |
| `chevrotain-11.0.3.min.js` | 11.0.3 | npm `chevrotain/lib/chevrotain.min.mjs` 原样复制（自包含 ESM），经 import map 提供给 `lib.js` 的 `chevrotain` 导出 | Apache-2.0（`chevrotain-LICENSE.txt`） |

酒馆助手脚本（`services/st/authorRuntime.ts`）会用到 MVU Zod 的全局 `z`；
断网时卡内脚本无法从 CDN 加载 zod，这里内置一份。

升级时替换文件并同步更新本表与 `cardApp.ts` / `authorRuntime.ts` 中的说明；不要修改文件内容。
lodash 的再生成命令（在 `webapp/` 下）：

```bash
./node_modules/.bin/esbuild node_modules/lodash-es/lodash.js \
  --bundle --format=iife --global-name=__lodashVendor --target=es2017 \
  --minify --legal-comments=none --outfile=src/services/st/vendor/lodash-4.18.1.min.js
```

生成后需在文件头手工补一行版权声明。
