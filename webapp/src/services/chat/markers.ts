/**
 * 工具输出富文本标记（对照旧版 images.js / audio.js）：
 * 生图与 TTS 的结果以首行标记携带 URL，参数以第二行单行 JSON 携带；
 * P 站搜图/精选以 [候] / [选] 行携带编号、pid、URL。
 * 这些是纯函数，工具卡与流式渲染共用。
 */

export type GeneratedImageMeta = {
  engine?: string
  engine_label?: string
  /** txt2img / img2img */
  mode?: string
  prompt?: string
  negative_prompt?: string
  model?: string
  sampler?: string
  schedule?: string
  steps?: number | string
  cfg_scale?: number | string
  width?: number | string
  height?: number | string
  seed?: number | string
  size?: string
  ref_count?: number
  refs?: string[]
  [key: string]: unknown
}

export type GeneratedAudioMeta = {
  speaker?: string
  voice_source?: string
  text_len?: number
  format?: string
  sample_rate?: number
  container?: string
  [key: string]: unknown
}

export type PixivCandidate = {
  index: number
  pid: string
  url: string
}

/** 搜索工具消息 id → {候选编号: 原图 URL} */
export type PixivReveal = Record<string, Record<number, string>>

const IMAGE_MARKER = /\[(?:图片已生成|图片)\]\s*(\S+)/
const IMAGE_META = /^\[图片参数\]\s*(\{.*\})\s*$/m
const AUDIO_MARKER = /\[(?:语音已生成|语音)\]\s*(\S+)/
const AUDIO_META = /^\[语音参数\]\s*(\{.*\})\s*$/m
const PX_UNREVEALED = /\[候\]\s+(\d+)\s+(\d+)\s+(\S+)/g
const PX_PICKED = /\[选\]\s+(\d+)\s+(\d+)\s+(\S+)/g

export const extractGeneratedImage = (text: string | null | undefined): string => {
  const match = IMAGE_MARKER.exec(text || '')
  return match ? match[1] : ''
}

/** 从工具输出中提取 [图片参数] 单行 JSON；非法 JSON 返回 null */
export const extractImageMeta = (text: string | null | undefined): GeneratedImageMeta | null => {
  const match = IMAGE_META.exec(text || '')
  if (!match) {
    return null
  }
  try {
    const parsed: unknown = JSON.parse(match[1])
    return parsed && typeof parsed === 'object' ? (parsed as GeneratedImageMeta) : null
  } catch {
    return null
  }
}

/** 图片标记输出：卡片只渲染图片，不显示任何指令文本 */
export const isImageMarkerText = (text: string | null | undefined): boolean =>
  /^\[(?:图片已生成|图片)\]/.test(text || '')

export const extractGeneratedAudio = (text: string | null | undefined): string => {
  const match = AUDIO_MARKER.exec(text || '')
  return match ? match[1] : ''
}

export const extractAudioMeta = (text: string | null | undefined): GeneratedAudioMeta | null => {
  const match = AUDIO_META.exec(text || '')
  if (!match) {
    return null
  }
  try {
    const parsed: unknown = JSON.parse(match[1])
    return parsed && typeof parsed === 'object' ? (parsed as GeneratedAudioMeta) : null
  } catch {
    return null
  }
}

/** 语音标记输出：卡片只渲染语音条，不显示指令文本 */
export const isAudioMarkerText = (text: string | null | undefined): boolean =>
  /^\[(?:语音已生成|语音)\]/.test(text || '')

export const isPixivCandidatesText = (text: string | null | undefined): boolean =>
  /^\[P站候选\]/.test(text || '')

export const isPixivPicksText = (text: string | null | undefined): boolean =>
  /^\[P站精选\]/.test(text || '')

const parsePixivLines = (text: string | null | undefined, pattern: RegExp): PixivCandidate[] => {
  const out: PixivCandidate[] = []
  pattern.lastIndex = 0
  let match = pattern.exec(text || '')
  while (match) {
    out.push({ index: parseInt(match[1], 10), pid: match[2], url: match[3] })
    match = pattern.exec(text || '')
  }
  return out
}

export const parsePixivCands = (text: string | null | undefined): PixivCandidate[] =>
  parsePixivLines(text, PX_UNREVEALED)

export const parsePixivPicks = (text: string | null | undefined): PixivCandidate[] =>
  parsePixivLines(text, PX_PICKED)

/**
 * 从按渲染顺序排列的全部工具条目建立 搜索工具id → {候选编号: 原图URL} 映射。
 * search_pixiv 开启一段候选墙，随后出现的 pick_pixiv 把精选原图填回该搜索。
 */
export const buildPixivReveal = (
  entries: Array<{ id: string; name: string; content?: string | null }>
): PixivReveal => {
  const map: PixivReveal = {}
  let current: string | null = null
  for (const entry of entries) {
    if (entry.name === 'search_pixiv') {
      current = entry.id
      map[current] = {}
    } else if (entry.name === 'pick_pixiv' && current) {
      for (const pick of parsePixivPicks(entry.content)) {
        map[current][pick.index] = pick.url
      }
    }
  }
  return map
}