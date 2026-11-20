/**
 * 卡片沙箱管理员策略（st_image / st_net）的面板工具函数。
 *
 * 域名白名单在面板里用多行文本编辑；这里按后端 StImageSettings/StNetSettings
 * 的清洗规则做一遍归一化（小写、去前导点、去重、丢弃含路径的项），保证回传
 * 的列表与管理员看到的输入一致。
 */

export const parseDomainList = (text: string): string[] => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of text.split(/[\s,，;；]+/)) {
    const host = raw.trim().toLowerCase().replace(/^\.+/, '')
    if (!host || host.includes('/') || host.length > 253 || seen.has(host)) {
      continue
    }
    seen.add(host)
    out.push(host)
  }
  return out
}

export const formatDomainList = (domains: unknown): string =>
  Array.isArray(domains) ? domains.filter((item) => typeof item === 'string').join('\n') : ''

/** 后端以字节存上限，面板用 MB 展示（保留一位小数，默认 5MB） */
export const bytesToMbText = (value: unknown): string =>
  typeof value === 'number'
    ? String(Math.round((value / 1048576) * 10) / 10)
    : '5'

/** 面板 MB 输入 → 后端字节；调用方负责先校验数值有限 */
export const mbTextToBytes = (text: string): number =>
  Math.round(Number(text) * 1024 * 1024)
