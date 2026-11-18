import { request } from './client.js'

/** 爱发电自动发卡：公开购买信息、用户订单领取与管理员订单管理（字段与后端 snake_case 对齐）。 */

export type AfdianPublicInfo = {
  enabled: boolean
  purchase_url: string
  purchase_label: string
}

/** 已发放并绑定到当前账号的订单（code 可直接兑换） */
export type MyAfdianOrder = {
  out_trade_no: string
  plan_title: string
  amount: number
  credits: number
  code: string
  redeemed: boolean
  issued_at: number
}

export type AfdianClaimResult = {
  out_trade_no: string
  code: string
  amount: number
  credits: number
}

export type AfdianOrderState =
  | 'received'
  | 'verify_failed'
  | 'issued'
  | 'pending_cap'
  | 'invalid'

export type AfdianOrderRecord = {
  out_trade_no: string
  afdian_user_id: string
  plan_id: string
  plan_title: string
  total_amount: number
  remark: string
  order_status: string
  state: AfdianOrderState
  credits: number
  cdk_code: string
  bound_uid: string
  email_sent: boolean
  email_error: string
  reason: string
  created_at: number
  verified_at: number
  issued_at: number
}

export type AfdianSummary = {
  cumulative: number
  cap: number
  pending: number
  issued: number
  total: number
}

export const fetchAfdianInfo = (): Promise<AfdianPublicInfo> =>
  request<AfdianPublicInfo>('/api/app/afdian')

export const fetchMyAfdianOrders = (): Promise<MyAfdianOrder[]> =>
  request<{ orders: MyAfdianOrder[] }>('/api/my/afdian/orders').then(
    (data) => data.orders ?? []
  )

/** 订单备注没填用户名时的兜底：用爱发电订单号领取 */
export const claimAfdianOrder = (outTradeNo: string): Promise<AfdianClaimResult> =>
  request<AfdianClaimResult>('/api/my/afdian/claim', {
    method: 'POST',
    body: JSON.stringify({ out_trade_no: outTradeNo }),
  })

export const fetchAdminAfdian = (): Promise<{
  summary: AfdianSummary
  orders: AfdianOrderRecord[]
}> => request<{ summary: AfdianSummary; orders: AfdianOrderRecord[] }>('/api/admin/afdian')

/** 重新走 query-order 校验并尝试发卡 */
export const verifyAfdianOrder = (
  outTradeNo: string
): Promise<{ result: { state: string; code?: string; reason?: string } }> =>
  request<{ result: { state: string; code?: string; reason?: string } }>(
    `/api/admin/afdian/orders/${encodeURIComponent(outTradeNo)}/verify`,
    { method: 'POST' }
  )

/** 超上限待补发的订单：上限调高后重新判断并补发 */
export const reissueAfdianOrder = (
  outTradeNo: string
): Promise<{ result: { state: string; code?: string; reason?: string } }> =>
  request<{ result: { state: string; code?: string; reason?: string } }>(
    `/api/admin/afdian/orders/${encodeURIComponent(outTradeNo)}/reissue`,
    { method: 'POST' }
  )
