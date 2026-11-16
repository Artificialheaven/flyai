/**
 * Smart Search 来源（P3 接入 DeepSeek 联网搜索时填充）。
 * 形状对齐 chat-demo 的 Source，Markdown 里的 [citation:N] 会被渲染成数字角标。
 */
export type CitationSource = {
  url: string
  title: string
  /** 优先使用服务端站点名，没有时使用域名（去掉 www.） */
  siteName: string
  /** 搜索结果为 RFC 3339，阅读链接为爬虫提取的原始日期 */
  publishedAt: string | null
  /** 明文摘要与站点图标；缺少元数据时为 null */
  snippet: string | null
  siteIcon: string | null
  /** 引用角标与搜索结果列表上的编号，从 1 开始 */
  index: number
}
