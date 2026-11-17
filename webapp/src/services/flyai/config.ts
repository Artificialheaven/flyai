import { request } from './client.js'

/** GET /api/config 的登录态公开视图（模型/工具/语音等；密钥均掩码或不下发）。 */

export type VoiceConfig = { name: string; id: string }

export type PublicTtsConfig = {
  enabled?: boolean
  resource_id?: string
  /** 管理员维护的音色清单（角色卡绑定下拉数据源） */
  voices?: VoiceConfig[]
  /** 未绑定时的默认音色 ID */
  default_speaker?: string
  /** 使用语音合成的最低权限等级 */
  min_level?: number
}

export type PublicConfig = {
  active_provider?: string
  tools?: Record<string, unknown>
  roleplay?: Record<string, unknown>
  image_generation?: Record<string, unknown>
  image_models?: { id: string; name: string; model: string; min_level?: number }[]
  stable_diffusion?: Record<string, unknown>
  tts?: PublicTtsConfig
  [key: string]: unknown
}

export const getPublicConfig = async (): Promise<PublicConfig> =>
  request<PublicConfig>('/api/config')

/** 音色下拉数据：角色卡 voice 为空表示跟随默认音色。 */
export const listTtsVoices = async (): Promise<{ voices: VoiceConfig[]; defaultSpeaker: string }> => {
  const cfg = await getPublicConfig()
  return {
    voices: cfg.tts?.voices ?? [],
    defaultSpeaker: cfg.tts?.default_speaker ?? '',
  }
}