import { request, uploadRequest } from '../flyai/client.js'
import type {
  ExtensionDetail,
  ExtensionHostState,
  ExtensionItem,
  ExtensionPanel,
  InstallResult,
} from './types.js'

export const listExtensions = async (): Promise<{
  extensions: ExtensionItem[]
  enabled: boolean
  admin: boolean
}> => request('/api/extensions')

export const getExtension = async (name: string): Promise<ExtensionDetail> =>
  request(`/api/extensions/${encodeURIComponent(name)}`)

export const installExtension = async (
  file: File,
  onProgress?: (percent: number) => void
): Promise<InstallResult> => {
  const form = new FormData()
  form.append('file', file, file.name)
  return uploadRequest<InstallResult>('/api/extensions/install', form, {
    onProgress: (progress) => onProgress?.(progress.percent),
  })
}

export const updateExtension = async (
  name: string,
  file: File,
  onProgress?: (percent: number) => void
): Promise<InstallResult> => {
  const form = new FormData()
  form.append('file', file, file.name)
  return uploadRequest<InstallResult>(
    `/api/extensions/${encodeURIComponent(name)}/update`,
    form,
    { onProgress: (progress) => onProgress?.(progress.percent) }
  )
}

export const uninstallExtension = async (name: string, purgeSettings = false): Promise<void> => {
  await request(`/api/extensions/${encodeURIComponent(name)}?purge_settings=${purgeSettings ? 'true' : 'false'}`, {
    method: 'DELETE',
  })
}

export const setExtensionEnabled = async (name: string, enabled: boolean): Promise<void> => {
  await request(`/api/extensions/${encodeURIComponent(name)}/${enabled ? 'enable' : 'disable'}`, {
    method: 'POST',
    body: enabled ? JSON.stringify({ enabled: true }) : undefined,
  })
}

export const setExtensionOrder = async (order: string[]): Promise<void> => {
  await request('/api/extensions/order', {
    method: 'PUT',
    body: JSON.stringify({ order }),
  })
}

export const getExtensionSettings = async (name: string): Promise<Record<string, unknown>> => {
  const data = await request<{ settings: Record<string, unknown> }>(
    `/api/extensions/${encodeURIComponent(name)}/settings`
  )
  return data.settings
}

export const putExtensionSettings = async (
  name: string,
  settings: Record<string, unknown>
): Promise<void> => {
  await request(`/api/extensions/${encodeURIComponent(name)}/settings`, {
    method: 'PUT',
    body: JSON.stringify({ settings }),
  })
}

export const listEnabledExtensions = async (): Promise<{ extensions: ExtensionHostState[]; enabled: boolean }> =>
  request('/api/extensions/enabled')

export const getExtensionPanel = async (name: string): Promise<ExtensionPanel> =>
  request(`/api/extensions/${encodeURIComponent(name)}/panel`)
