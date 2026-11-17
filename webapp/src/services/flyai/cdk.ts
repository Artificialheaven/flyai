import { request } from './client.js'
import type { QuotaView } from './admin.js'

/** CDK 兑换码：用户兑换 + 管理员批量管理（字段与后端 snake_case 对齐）。 */

export type CdkStatus = '' | 'usable' | 'expired' | 'exhausted' | 'disabled'

export type CdkView = {
  code: string
  /** 兑换后增加的额度（点数） */
  amount: number
  /** 全站可兑换总次数，<=0 表示不限 */
  max_uses: number
  /** 已兑换人次（每用户限一次） */
  used: number
  /** 有效期（"YYYY-MM-DD"，空=永久） */
  expires_at: string
  created_at: number
  disabled: boolean
  status: CdkStatus
}

export type RedeemResult = { amount: number; quota: QuotaView }

export type CdkBatchPayload = {
  amount: number
  max_uses: number
  expires_at: string
}

export type CdkImportResult = {
  added: string[]
  duplicates: string[]
  invalid: string[]
}

/** 普通用户兑换：成功返回本次额度和最新余额 */
export const redeemCdk = (code: string): Promise<RedeemResult> =>
  request<RedeemResult>('/api/my/redeem', {
    method: 'POST',
    body: JSON.stringify({ code }),
  })

export const listCdks = (status: CdkStatus = ''): Promise<CdkView[]> =>
  request<{ cdks: CdkView[] }>(
    `/api/admin/cdk${status ? `?status=${encodeURIComponent(status)}` : ''}`
  ).then((data) => data.cdks ?? [])

export const generateCdks = (
  payload: CdkBatchPayload & { count: number }
): Promise<CdkView[]> =>
  request<{ cdks: CdkView[] }>('/api/admin/cdk/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).then((data) => data.cdks ?? [])

export const importCdks = (payload: CdkBatchPayload & { codes: string }): Promise<CdkImportResult> =>
  request<CdkImportResult>('/api/admin/cdk/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })

/** 取出一张可用 CDK（不删除）；无可用时返回 null */
export const extractCdk = (): Promise<CdkView | null> =>
  request<{ cdk: CdkView | null }>('/api/admin/cdk/extract').then((data) => data.cdk ?? null)

export const disableCdk = (code: string): Promise<CdkView> =>
  request<{ cdk: CdkView }>(`/api/admin/cdk/${encodeURIComponent(code)}/disable`, {
    method: 'POST',
  }).then((data) => data.cdk)

export const deleteCdk = (code: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/admin/cdk/${encodeURIComponent(code)}`, { method: 'DELETE' })

/** 导出 CSV 文本 */
export const exportCdks = (): Promise<string> =>
  request<{ text: string }>('/api/admin/cdk/export').then((data) => data.text)