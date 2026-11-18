import { create } from 'zustand'

/** 设置弹窗的分组：通用 / 用量 / 模型 / 功能 / 记忆 / 数据 / 邀请 / 用户（管理员）/ 系统（管理员）/ 生态接入（管理员）/ CDK（管理员）/ 权限门槛（管理员）/ DeepSeek（管理员）/ 邮件服务（管理员）/ 公告（管理员） */
export type SettingsSection =
  | 'general'
  | 'usage'
  | 'models'
  | 'features'
  | 'memory'
  | 'data'
  | 'invite'
  | 'users'
  | 'system'
  | 'integration'
  | 'cdk'
  | 'access'
  | 'deepseek'
  | 'email'
  | 'announcement'

/** 设置弹窗：侧边栏底部、401 提示等都可以打开；section 为打开时选中的分组 */
export const useSettingsDialog = create(() => ({
  open: false,
  section: 'general' as SettingsSection,
}))

export const openSettings = (section: SettingsSection) =>
  useSettingsDialog.setState({ open: true, section })

export const closeSettings = () => useSettingsDialog.setState({ open: false })
