import { request } from './client.js'

export type InviteUse = {
  uid: string
  name: string
  /** epoch 秒 */
  at: number
}

export type InviteView = {
  code: string
  owner: string
  max_uses: number
  used: number
  remaining: number
  created_at: number
  uses: InviteUse[]
  status: 'usable' | 'exhausted'
}

export type AdminInviteView = InviteView & {
  owner_name: string
  owner_missing: boolean
}

export const getMyInvite = (): Promise<InviteView | null> =>
  request<{ invite: InviteView | null }>('/api/my/invite').then((data) => data.invite ?? null)

export const regenerateMyInvite = (): Promise<InviteView> =>
  request<{ invite: InviteView }>('/api/my/invite/regenerate', { method: 'POST' }).then(
    (data) => data.invite
  )

/** 管理员设置自己的邀请码：code 留空=随机生成 */
export const setAdminInvite = (code: string, maxUses: number): Promise<InviteView> =>
  request<{ invite: InviteView }>('/api/admin/invites/mine', {
    method: 'PUT',
    body: JSON.stringify({ code, max_uses: maxUses }),
  }).then((data) => data.invite)

export const listAdminInvites = (): Promise<AdminInviteView[]> =>
  request<{ invites: AdminInviteView[] }>('/api/admin/invites').then(
    (data) => data.invites ?? []
  )

export const deleteAdminInvite = (code: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/admin/invites/${encodeURIComponent(code)}`, {
    method: 'DELETE',
  })
