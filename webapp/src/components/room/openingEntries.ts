import type { LoreEntry } from '../../services/flyai/types.js'

/**
 * 开局（开场设定）候选条目：世界书标题以「开局」「序章」开头且内容非空。
 * 与后端 flyai/lore/opening.py 的识别规则保持一致。
 */
const OPENING_TITLE_RE = /^\s*(?:开局|序章)/

export type OpeningOption = { title: string; preview: string }

export const openingOptions = (table: LoreEntry[] | null | undefined): OpeningOption[] => {
  const out: OpeningOption[] = []
  const seen = new Set<string>()
  for (const entry of table ?? []) {
    const title = (entry.title ?? '').trim()
    if (!title || seen.has(title) || !OPENING_TITLE_RE.test(title)) {
      continue
    }
    const content = (entry.content ?? '').trim()
    if (!content) {
      continue
    }
    seen.add(title)
    out.push({ title, preview: content.replace(/\s+/g, ' ').slice(0, 40) })
  }
  return out
}
