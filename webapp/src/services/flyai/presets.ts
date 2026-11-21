import { apiUrl, authHeaders, FlyApiError, readDetail, request } from './client.js'
import { downloadBlob } from '../native.js'
import { invalidateCatalog, loadCatalog, updateCatalog } from './catalogCache.js'
import type { Preset, PresetImportResult } from './types.js'

/**
 * 模型预设：私有列表 / 启用 / 复制 / 删除 / 导入导出。
 * 启用 id 存在用户设置里，下一次聊天请求即生效（后端按请求读取）。
 */

export type PresetCatalog = { presets: Preset[]; activeId: string }

export const listPresets = (options?: { force?: boolean }): Promise<PresetCatalog> =>
  loadCatalog(
    'presets',
    async () => {
      const data = await request<{ presets?: Preset[]; active_preset_id?: string }>(
        '/api/presets'
      )
      return { presets: data.presets ?? [], activeId: data.active_preset_id ?? '' }
    },
    options
  )

/** 传空 id 表示停用当前预设；返回后端确认后的启用 id */
export const setActivePreset = async (id: string): Promise<string> => {
  const data = await request<{ active_preset_id?: string }>('/api/presets/active', {
    method: 'POST',
    body: JSON.stringify({ id }),
  })
  const activeId = data.active_preset_id ?? ''
  // 就地同步缓存里的启用 id，避免下拉框回跳到旧值
  updateCatalog<PresetCatalog>('presets', (current) => ({ ...current, activeId }))
  return activeId
}

export const clonePreset = async (id: string): Promise<Preset> => {
  const preset = await request<Preset>(`/api/presets/${encodeURIComponent(id)}/clone`, {
    method: 'POST',
  })
  invalidateCatalog('presets')
  return preset
}

export type PresetBlockStateChange = { index: number; enabled: boolean }

/** 批量开关预设内 prompt 块（单选组一次提交多条）；返回更新后的预设 */
export const setPresetBlockStates = async (
  id: string,
  changes: PresetBlockStateChange[]
): Promise<Preset> => {
  const preset = await request<Preset>(
    `/api/presets/${encodeURIComponent(id)}/blocks`,
    { method: 'PUT', body: JSON.stringify({ changes }) }
  )
  invalidateCatalog('presets')
  return preset
}

/** 开关预设内单条正则（按列表下标），立即落盘；返回更新后的预设 */
export const setPresetRegexDisabled = async (
  id: string,
  index: number,
  disabled: boolean
): Promise<Preset> => {
  const preset = await request<Preset>(
    `/api/presets/${encodeURIComponent(id)}/regex/${index}`,
    { method: 'PUT', body: JSON.stringify({ disabled }) }
  )
  invalidateCatalog('presets')
  return preset
}

export const deletePreset = async (id: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/presets/${encodeURIComponent(id)}`, { method: 'DELETE' })
  invalidateCatalog('presets')
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
  const result = await request<PresetImportResult>('/api/presets/import', {
    method: 'POST',
    body: form,
    formData: true,
  })
  invalidateCatalog('presets')
  return result
}
