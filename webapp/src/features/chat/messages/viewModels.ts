import type { AssistantChain } from '../../../services/chat/history.js'
import type { LiveAssistant, ToolStatus } from '../../../services/chat/streamReducer.js'
import type { MessageImage, RecallItem } from '../../../types/session.js'
import {
  extractAudioMeta,
  extractGeneratedAudio,
  extractGeneratedImage,
  extractImageMeta,
  isAudioMarkerText,
  isImageMarkerText,
  isPixivCandidatesText,
  isPixivPicksText,
  parsePixivCands,
  parsePixivPicks,
  type GeneratedAudioMeta,
  type GeneratedImageMeta,
  type PixivCandidate,
  type PixivReveal,
} from './markers.js'
import type { ToolView } from './ToolCard.js'

/** 工具卡正文：文本可展开；图片/语音/P 站候选墙按标记结构化渲染 */
export type ToolBody =
  | { kind: 'text'; text: string }
  | { kind: 'image'; src: string; meta: GeneratedImageMeta | null }
  | { kind: 'audio'; src: string; meta: GeneratedAudioMeta | null }
  | {
      kind: 'pixiv'
      candidates: PixivCandidate[]
      revealed: Record<number, string>
      /** 流式中：未精选候选显示模糊缩略图；历史里改为无网络请求的毛玻璃占位 */
      live: boolean
    }

/** 历史链与流式卡片的统一渲染模型，气泡组件不感知数据来源 */
export type AssistantView = {
  key: string
  name: string | null
  thinking: string
  content: string
  images: MessageImage[]
  tools: ToolView[]
  recalls: RecallItem[][]
  /** 流式进行中：驱动占位、思考块默认展开与 Markdown 节流 */
  streaming: boolean
}

/**
 * 单条工具输出 → 卡片模型：预览/历史统一按首行标记分流；
 * 图片/语音指令句与路径不外显，P 站候选由 reveal 映射决定哪些位置已出新原图。
 */
const buildToolView = ({
  id,
  name,
  status,
  text,
  statusText,
  live,
  reveal,
}: {
  id: string
  name: string
  status: ToolStatus
  text: string | null
  statusText?: string
  live: boolean
  reveal: Record<number, string>
}): ToolView => {
  const base: ToolView = { id, name, status }
  if (!text) {
    return { ...base, statusText: statusText ?? null }
  }
  if (isPixivCandidatesText(text)) {
    const candidates = parsePixivCands(text)
    if (candidates.length) {
      return {
        ...base,
        statusText: live ? `找到 ${candidates.length} 张候选，AI 筛选中…` : null,
        body: { kind: 'pixiv', candidates, revealed: reveal, live },
      }
    }
  } else if (isPixivPicksText(text)) {
    const picks = parsePixivPicks(text)
    return {
      ...base,
      // 精选已经并入搜索卡网格；本卡只显示数量（历史回放时）
      statusText: !live && picks.length ? `已精选 ${picks.length} 张` : null,
    }
  } else if (isAudioMarkerText(text)) {
    const src = extractGeneratedAudio(text)
    if (src) {
      return { ...base, statusText: statusText ?? null, body: { kind: 'audio', src, meta: extractAudioMeta(text) } }
    }
  } else if (isImageMarkerText(text)) {
    const src = extractGeneratedImage(text)
    if (src) {
      return { ...base, statusText: statusText ?? null, body: { kind: 'image', src, meta: extractImageMeta(text) } }
    }
  }
  return {
    ...base,
    statusText: statusText ?? null,
    body: { kind: 'text', text },
  }
}

export const chainToView = (chain: AssistantChain, reveal?: PixivReveal): AssistantView => ({
  key: chain.key,
  name: chain.name,
  thinking: chain.thinking,
  content: chain.contents.join('\n\n'),
  images: chain.images,
  tools: chain.tools.map((tool) =>
    buildToolView({
      id: tool.id,
      name: tool.name,
      status: tool.ok ? 'done' : 'error',
      text: tool.content,
      live: false,
      reveal: reveal?.[tool.id] ?? {},
    })
  ),
  recalls: chain.recalls,
  streaming: false,
})

export const liveToView = (card: LiveAssistant, reveal?: PixivReveal): AssistantView => ({
  key: card.key,
  name: card.name || 'AI',
  thinking: card.thinking,
  content: card.content,
  images: [],
  tools: card.tools.map((tool) => {
    const running = tool.status === 'pending' || tool.status === 'running'
    return buildToolView({
      id: tool.id,
      name: tool.name,
      status: tool.status,
      // 指令句只按 output/preview 判定；statusText 只用于状态行，避免重复成正文
      text: tool.output ?? tool.preview ?? null,
      // tool_end 后保留的启动文案可能过期，只在执行中展示
      statusText: running ? tool.statusText : undefined,
      live: true,
      reveal: reveal?.[tool.id] ?? {},
    })
  }),
  recalls: card.recallGroups,
  streaming: true,
})