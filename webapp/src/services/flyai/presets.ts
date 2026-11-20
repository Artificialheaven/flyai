import { apiUrl, authHeaders, FlyApiError, readDetail, request } from './client.js'
import { downloadBlob } from '../native.js'
import type { Preset, PresetImportResult } from './types.js'

/**
 * 模型预设：私有列表 / 启用 / 复制 / 删除 / 导入导出。
 * 启用 id 存在用户设置里，下一次聊天请求即生效（后端按请求读取）。
 */

export const listPresets = async (): Promise<{ presets: Preset[]; activeId: string }> => {
  const data = await request<{ presets?: Preset[]; active_preset_id?: string }>('/api/presets')
  return { presets: data.presets ?? [], activeId: data.active_preset_id ?? '' }
}

/** 传空 id 表示停用当前预设；返回后端确认后的启用 id */
export const setActivePreset = async (id: string): Promise<string> => {
  const data = await request<{ active_preset_id?: string }>('/api/presets/active', {
    method: 'POST',
    body: JSON.stringify({ id }),
  })
  return data.active_preset_id ?? ''
}

export const clonePreset = async (id: string): Promise<Preset> =>
  request<Preset>(`/api/presets/${encodeURIComponent(id)}/clone`, { method: 'POST' })

/** 开关预设内单条正则（按列表下标），立即落盘；返回更新后的预设 */
export const setPresetRegexDisabled = async (
  id: string,
  index: number,
  disabled: boolean
): Promise<Preset> =>
  request<Preset>(`/api/presets/${encodeURIComponent(id)}/regex/${index}`, {
    method: 'PUT',
    body: JSON.stringify({ disabled }),
  })

export const deletePreset = async (id: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/presets/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

const fetchBlob = async (path: string): Promise<Blob> => {
  const res = await fetch(apiUrl(path), { headers: authHeaders() })
  if (!res.ok) {
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return await res.blob()
}

const safeName = (name: string) =>
  (name || 'preset').replace(/[\\/:*?"<>|]+/g, '_').trim().slice(0, 60) || 'preset'

export const exportPreset = async (preset: Preset): Promise<void> => {
  const blob = await fetchBlob(`/api/presets/${encodeURIComponent(preset.id)}/export`)
  await downloadBlob(blob, `${safeName(preset.name)}.json`)
}

/** 酒馆 OpenAI 预设（.json/.txt，脏 JSON 容错）或 FlyAI 预设信封 */
export const importPreset = async (file: File): Promise<PresetImportResult> => {
  const form = new FormData()
  form.append('file', file, file.name)
  return request<PresetImportResult>('/api/presets/import', {
    method: 'POST',
    body: form,
    formData: true,
  })
}
