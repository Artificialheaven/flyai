import { request } from './client.js'

/** 管理员/模型管理相关接口的类型与薄封装（字段与后端 snake_case 原样对齐）。 */

/** 分时段价格：时间窗/日期区间留空表示不限；按顺序匹配，先命中先生效 */
export type PricePeriod = {
  name?: string
  cache_hit_input?: number
  input?: number
  output?: number
  /** "HH:MM" 每日时间窗，支持跨午夜；需与 daily_end 成对 */
  daily_start?: string
  daily_end?: string
  /** "YYYY-MM-DD" 日期区间（含端点） */
  date_start?: string
  date_end?: string
}

/** 按 token 计费的价格（每 1M token，单位：点数） */
export type TokenPricing = {
  cache_hit_input: number
  input: number
  output: number
  periods?: PricePeriod[]
}

/** GET /api/config 返回的模型视图：本人自建含 base_url/掩码 api_key；他人共享只有精简字段 */
export type ProviderView = {
  id: string
  model: string
  type?: string
  base_url?: string
  api_key?: string
  params?: Record<string, unknown>
  thinking_params?: Record<string, unknown> | null
  thinking_off_params?: Record<string, unknown> | null
  vision?: boolean
  owner?: string
  shared?: boolean
  min_level?: number
  rate?: number
  /** null 表示按每轮 rate 计费 */
  pricing?: TokenPricing | null
}

export type ImageModelView = {
  id: string
  name: string
  model: string
  base_url?: string
  api_key?: string
  owner?: string
  shared?: boolean
  min_level?: number
}

export type QuotaView = {
  /** -1 或 unlimited 表示不限次数 */
  total: number
  used: number
  remaining: number | null
  unlimited?: boolean
}

type TtsGlobalView = {
  enabled?: boolean
  engine?: string
  min_level?: number
  resource_id?: string
  voices?: { name: string; speaker: string }[]
  api_key?: string
}

/** 功能权限门槛（GET /api/config 对所有登录用户下发，前端据此提示不可用功能） */
export type AccessSettingsView = {
  browser_min_level: number
  pixiv_min_level: number
  galgame_min_level: number
  agent_min_level: number
}

/** DeepSeek 官方服务（仅 admin 可见；api_key 为掩码，key_set 表示服务端已配置 Key） */
export type DeepSeekSettingsView = {
  enabled: boolean
  api_key: string
  key_set: boolean
  base_url: string
  proxy: string
  timeout_seconds: number
  search_enabled: boolean
  fetch_enabled: boolean
  tts_enabled: boolean
  asr_enabled: boolean
  /** 单价：元 / 1000 次成功调用 */
  search_price: number
  fetch_price: number
  /** 单价：元 / 10000 计费字符 */
  tts_price: number
  /** 单价：元 / 小时 */
  asr_price: number
  tts_voice: string
}

/** 邮件服务（仅 admin 可见；password 为掩码，password_set 表示服务端已存授权码） */
export type EmailSettingsView = {
  enabled: boolean
  host: string
  port: number
  encryption: 'ssl' | 'starttls' | 'none'
  username: string
  password: string
  password_set: boolean
  from_name: string
  from_address: string
  code_ttl_seconds: number
  resend_interval_seconds: number
  hourly_limit: number
}

/** 启动公告：每次打开应用（含登录页）弹出 */
export type AnnouncementView = {
  enabled: boolean
  title: string
  content: string
}

/** 爱发电方案：amount=0 时按订单实付金额，最终点数 = 基数 × ratio */
export type AfdianPlanView = {
  plan_id: string
  name: string
  amount: number
  ratio: number
}

/** 爱发电自动发卡（仅 admin 可见；token 为掩码，webhook_token 为明文以便拼回调地址） */
export type AfdianSettingsView = {
  enabled: boolean
  user_id: string
  token: string
  token_set: boolean
  api_base: string
  webhook_token: string
  webhook_token_set: boolean
  public_base_url: string
  /** 累计销售额上限（元）；-1 表示不限 */
  max_total_amount: number
  default_amount: number
  default_ratio: number
  plans: AfdianPlanView[]
  purchase_url: string
  purchase_label: string
}

/** GET /api/config 的子集：模型管理与系统默认值需要的字段 */
export type AdminConfig = {
  active_provider?: string
  providers: ProviderView[]
  image_models: ImageModelView[]
  tools: { enabled: boolean; max_iterations: number; timeout_seconds: number }
  roleplay: {
    format_enabled: boolean
    attributes?: { enabled: boolean }
    intro_chars?: number
    background_chars?: number
    world_chars?: number
    mes_example_chars?: number
    pinned_chars?: number
  }
  image_generation: { enabled: boolean; default_size: string }
  pixiv: { enabled: boolean; r18_allowed: boolean; candidate_num: number; api_base?: string; proxy?: string }
  web_search: { enabled: boolean; results_num?: number; max_page_chars?: number; engine?: string }
  browser: { enabled: boolean; headless?: boolean; proxy?: string }
  galgame: { enabled: boolean; max_loops: number }
  tts: TtsGlobalView
  loop: { enabled: boolean }
  /** 权限门槛：所有登录用户可见 */
  access?: AccessSettingsView
  /** DeepSeek 服务：仅 admin 视图包含 */
  deepseek?: DeepSeekSettingsView
  /** 邀请码注册设置：仅 admin 视图包含 */
  registration?: { default_quota: number; require_invite?: boolean }
  /** 启动公告：仅 admin 视图包含（展示内容另有公开接口） */
  announcement?: AnnouncementView
  /** 邮件服务（注册验证码/找回密码）：仅 admin 视图包含 */
  email?: EmailSettingsView
  /** 爱发电自动发卡：仅 admin 视图包含 */
  afdian?: AfdianSettingsView
  [key: string]: unknown
}

/** 用户个人设置（GET/PUT /api/my/settings）；覆盖字段 null = 跟随管理员全局默认 */
export type MySettings = {
  keep_turns: number
  auto_recall: boolean
  auto_recall_max: number
  extract_every_turns: number
  lore_auto_inject: boolean
  max_tokens: number | null
  active_preset_id?: string
  tools_enabled: boolean | null
  tool_max_iterations: number | null
  tool_timeout_seconds: number | null
  roleplay_format_enabled: boolean | null
  roleplay_attributes_enabled: boolean | null
  intro_chars: number | null
  background_chars: number | null
  world_chars: number | null
  mes_example_chars: number | null
  pinned_chars: number | null
  web_enabled: boolean | null
  browser_enabled: boolean | null
  pixiv_enabled: boolean | null
  image_enabled: boolean | null
  galgame_enabled: boolean | null
  galgame_max_loops: number | null
}

export type MySettingsPayload = Omit<MySettings, 'active_preset_id'>

/** 应用背景：电脑与手机各一套（theme=跟随主题 / solid=纯色 / image=图片） */
export type BackgroundMode = 'theme' | 'solid' | 'image'
/** opacity：背景图层不透明度 0-100（100=完全不透明） */
export type DeviceBackground = {
  mode: BackgroundMode
  color: string
  image: string
  opacity: number
}
export type BackgroundSettings = {
  desktop: DeviceBackground
  mobile: DeviceBackground
}
/** PUT 为补丁语义：缺省字段保持不变，空串清空 */
export type BackgroundPatch = {
  device: 'desktop' | 'mobile'
  mode?: BackgroundMode
  color?: string
  image?: string
  opacity?: number
}

export type ProviderPayload = {
  id?: string
  type?: string
  base_url?: string
  api_key?: string
  model?: string
  params?: Record<string, unknown>
  vision?: boolean
  rate?: number | null
  /** null 保持原值；{} 关闭按 token 计费；对象为完整价格配置 */
  pricing?: TokenPricing | Record<string, never> | null
}

export type ImageModelPayload = {
  id?: string
  name?: string
  base_url?: string
  api_key?: string
  model?: string
}

export type SharePayload = { shared: boolean; min_level: number }

export type AdminUser = {
  id: string
  name: string
  email?: string
  level: number
  /** ISO 本地时间；旧后端/管理员新建的用户可能缺省 */
  created_at?: string
  /** 上次登录时间；空串表示从未登录 */
  last_login_at?: string
  quota: QuotaView | null
}

/** 用量流水单日聚合（cost 单位：点数） */
export type UsageDay = {
  date: string
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number
  messages: number
}

export type UsageTotals = Omit<UsageDay, 'date'>

/** 按项目（kind）汇总的额度消耗；cost 单位：点数 */
export type UsageCategory = {
  /** chat = 对话 token；search/fetch/tts/asr = 按次/字符/秒计费 */
  kind: string
  cost: number
  quantity: number
  unit: string
  /** 计费调用次数；chat 取消息条数 */
  calls: number
  engines: string[]
}

export type MyUsage = {
  days: UsageDay[]
  totals: UsageTotals
  today: string
  categories: UsageCategory[]
}

export const getMyQuota = (): Promise<QuotaView> =>
  request<QuotaView | { unlimited: true }>('/api/my/quota').then((data) =>
    'unlimited' in data ? { total: -1, used: 0, remaining: null, unlimited: true } : data
  )

/** 近 N 天用量（按业务时区聚合；新数据起算，未回填历史） */
export const getMyUsage = (days = 7): Promise<MyUsage> =>
  request<MyUsage>(`/api/my/usage?days=${days}`)

export const getMySettings = (): Promise<MySettings> =>
  request<MySettings>('/api/my/settings')

export const putMySettings = (payload: MySettingsPayload): Promise<MySettings> =>
  request<MySettings>('/api/my/settings', { method: 'PUT', body: JSON.stringify(payload) })

export const getMyBackground = (): Promise<BackgroundSettings> =>
  request<BackgroundSettings>('/api/my/settings/background')

export const putMyBackground = (patch: BackgroundPatch): Promise<BackgroundSettings> =>
  request<BackgroundSettings>('/api/my/settings/background', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })

export const getConfig = (): Promise<AdminConfig> => request<AdminConfig>('/api/config')

/** 管理员切换全局默认模型；普通用户的后端仅做可见性校验，选择由前端本地保存 */
export const activateProvider = (providerId: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/config/activate/${encodeURIComponent(providerId)}`, {
    method: 'POST',
  })

export const testProvider = (providerId: string): Promise<{ ok: boolean; message: string }> =>
  request<{ ok: boolean; message: string }>('/api/config/test', {
    method: 'POST',
    body: JSON.stringify({ provider_id: providerId }),
  })

/** 用一次极短 TTS 探测「DeepSeek 服务」已保存的 Key（不产生用量计费） */
export const testDeepSeek = (): Promise<{ ok: boolean; message: string }> =>
  request<{ ok: boolean; message: string }>('/api/config/test/deepseek', {
    method: 'POST',
  })

/** 用已保存的邮件配置发一封测试信（收件人为空时后端回落到账号邮箱） */
export const testEmail = (to: string): Promise<{ ok: boolean; message: string }> =>
  request<{ ok: boolean; message: string }>('/api/config/test/email', {
    method: 'POST',
    body: JSON.stringify({ to }),
  })

export const createProvider = (payload: ProviderPayload): Promise<ProviderView> =>
  request<ProviderView>('/api/models/providers', {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const updateProvider = (id: string, payload: ProviderPayload): Promise<ProviderView> =>
  request<ProviderView>(`/api/models/providers/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })

export const deleteProvider = (id: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/models/providers/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

export const shareProvider = (id: string, payload: SharePayload): Promise<ProviderView> =>
  request<ProviderView>(`/api/models/providers/${encodeURIComponent(id)}/share`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const createImageModel = (payload: ImageModelPayload): Promise<ImageModelView> =>
  request<ImageModelView>('/api/models/images', {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const updateImageModel = (id: string, payload: ImageModelPayload): Promise<ImageModelView> =>
  request<ImageModelView>(`/api/models/images/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })

export const deleteImageModel = (id: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/models/images/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

export const shareImageModel = (id: string, payload: SharePayload): Promise<ImageModelView> =>
  request<ImageModelView>(`/api/models/images/${encodeURIComponent(id)}/share`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const listUsers = (): Promise<AdminUser[]> =>
  request<{ users: AdminUser[] }>('/api/auth/users').then((data) => data.users ?? [])

export const createUser = (payload: {
  name: string
  password: string
  level: number
  quota: number
}): Promise<AdminUser> =>
  request<AdminUser>('/api/auth/users', { method: 'POST', body: JSON.stringify(payload) })

export const updateUser = (
  uid: string,
  payload: { level?: number; quota?: number }
): Promise<{ ok: boolean; level?: number; quota?: number; quota_view?: QuotaView | null }> =>
  request<{ ok: boolean; level?: number; quota?: number; quota_view?: QuotaView | null }>(
    `/api/auth/users/${encodeURIComponent(uid)}`,
    { method: 'PATCH', body: JSON.stringify(payload) }
  )

export const resetUserPassword = (
  uid: string,
  password: string
): Promise<{ ok: boolean; revoked_sessions: number }> =>
  request<{ ok: boolean; revoked_sessions: number }>(
    `/api/auth/users/${encodeURIComponent(uid)}/password`,
    { method: 'PATCH', body: JSON.stringify({ password }) }
  )

export const deleteUser = (uid: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/auth/users/${encodeURIComponent(uid)}`, { method: 'DELETE' })

/** PUT /api/config 只解析下发段；这里用于保存系统默认值（管理员） */
export const putConfig = (payload: Record<string, unknown>): Promise<AdminConfig> =>
  request<AdminConfig>('/api/config', { method: 'PUT', body: JSON.stringify(payload) })