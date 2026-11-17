import { FlyApiError, openStream, request } from '../flyai/client.js'

/**
 * 语音接口（走 FlyAI 后端，DeepSeek Key 由管理员统一配置）：
 * - ASR：POST /api/speech/asr，multipart 上传 WAV / Ogg Opus，一次返回全文
 * - TTS：POST /api/speech/tts，返回音频字节流（长文本由后端分块合成）
 * 鉴权用登录态 Bearer 令牌。
 */

export type TtsVoice = 'stella' | 'tide'

/** TTS 输出的裸 PCM 参数：24kHz、单声道、16-bit 小端 */
export const TTS_SAMPLE_RATE = 24000

/** 语音识别：audio 为 WAV（单声道 16-bit PCM）或 Ogg Opus，时长不超过 120 秒、大小不超过 8 MiB */
export const transcribe = async (audio: Blob, signal?: AbortSignal): Promise<string> => {
  const form = new FormData()
  form.append('file', audio, 'recording.wav')
  const body = await request<{ text?: unknown }>('/api/speech/asr', {
    method: 'POST',
    body: form,
    formData: true,
    signal,
  })
  if (!body || typeof body.text !== 'string') {
    throw new FlyApiError(200, '语音识别返回格式异常')
  }
  return body.text
}

/** 语音合成：返回裸 PCM 字节流；后端负责切块、计费与 DeepSeek→豆包回退 */
export const synthesize = async (
  text: string,
  voice: TtsVoice,
  signal: AbortSignal
): Promise<ReadableStream<Uint8Array>> => {
  const response = await openStream('/api/speech/tts', { text, voice }, signal)
  if (!response.body) {
    throw new FlyApiError(502, '语音合成未返回音频数据')
  }
  return response.body
}