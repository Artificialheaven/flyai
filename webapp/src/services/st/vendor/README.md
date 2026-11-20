# 内置第三方前端库

卡内整页应用常从 CDN 加载 jQuery；断网或屏蔽 CDN 时状态栏脚本会初始化失败。
这里保存一份官方构建，由 `services/st/cardApp.ts` 在卡片引用 jQuery 时以内联
`<script>` 注入沙箱文档，不再发起网络请求。

| 文件 | 版本 | 来源 | 许可 |
|---|---|---|---|
| `jquery-3.6.0.min.js` | 3.6.0 | `https://code.jquery.com/jquery-3.6.0.min.js` | MIT（文件头保留版权声明） |

升级时替换文件并同步更新本表与 `cardApp.ts` 中的说明；不要修改文件内容。
