import { request } from './client.js'

export type DeepSeekBalance = {
  currency: string
  total: string
  granted: string
  topped_up: string
}

export type DeepSeekStatus = {
  configured: boolean
  provider_id: string
  model: string
  key_masked: string
  is_official_active: boolean
  platform: { sign_in: string; api_keys: string; top_up: string; usage: string }
  admin: boolean
}

export const getDeepSeekStatus = (): Promise<DeepSeekStatus> =>
  request<DeepSeekStatus>('/api/deepseek/status')

export const getDeepSeekBalance = (): Promise<{
  configured: boolean
  provider_id?: string
  available?: boolean
  balances: DeepSeekBalance[]
}> => request('/api/deepseek/balance')

export const connectDeepSeek = (
  apiKey: string,
  model: string,
  activate = true
): Promise<{ ok: boolean; provider_id: string; model: string; key_masked: string; activated: boolean }> =>
  request('/api/deepseek/connect', {
    method: 'POST',
    body: JSON.stringify({ api_key: apiKey, model, activate }),
  })
