import { Select, type SelectOption } from '../components/base/Select.js'
import { useUi, type ThemePreference } from '../stores/ui.js'

const OPTIONS: SelectOption<ThemePreference>[] = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
  { value: 'system', label: '跟随系统' },
]

/** 主题三态选择器，写回与旧站共用的 flyai-theme 键 */
export const ThemeSelect = ({ className }: { className?: string }) => {
  const themePreference = useUi((state) => state.themePreference)
  const setThemePreference = useUi((state) => state.setThemePreference)
  return (
    <Select
      value={themePreference}
      options={OPTIONS}
      onChange={setThemePreference}
      ariaLabel="切换主题"
      className={className}
    />
  )
}
