/**
 * 直连 DeepSeek 官方 API 的两个旁路能力（不经过 FlyAI 后端）：
 *  - P1 语音输入 ASR：POST {baseUrl}/v1/tools/asr，multipart WAV
 *  - P3 Smart Search（本文件先留通道，UI 后续接入）
 * 凭证只存在本地 runtimeConfig，绝不上送 FlyAI 服务器。
 */
import { getConfig } from '../runtimeConfig.js'

export class DeepSeekConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DeepSeekConfigError'
  }
}

const deepSeekOrigin = (): string => {
  const { baseUrl } = getConfig().deepseek
  return (baseUrl || '').replace(/\/+$/, '')
}

/** 语音转文字：录音 WAV → 识别文本；未配置 apiKey 时抛 DeepSeekConfigError 供 UI 引导 */
export const transcribe = async (audio: Blob, signal?: AbortSignal): Promise<string> => {
  const { apiKey } = getConfig().deepseek
  if (!apiKey) {
    throw new DeepSeekConfigError('未配置 DeepSeek API Key，请在设置中填写后再使用语音输入')
  }
  const form = new FormData()
  form.append('file', audio, 'recording.wav')
  const resp = await fetch(`${deepSeekOrigin()}/v1/tools/asr`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}` },
    body: form,
    signal,
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`语音识别失败（HTTP ${resp.status}）${detail ? `：${detail.slice(0, 200)}` : ''}`)
  }
  const body = (await resp.json()) as { text?: unknown }
  return typeof body.text === 'string' ? body.text : ''
}
