import { useMemo, useState } from 'react'
import clsx from 'clsx'
import { useTranslate, type TranslationKey } from '../../i18n/index.js'
import { setPresetBlockStates } from '../../services/flyai/presets.js'
import type { Preset } from '../../services/flyai/types.js'
import {
  groupPresetBlocks,
  type PresetEntry,
  type PresetEntryGroup,
  type PresetGroupKind,
} from '../../services/st/presetBlocks.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import styles from './Lore.module.css'

const GROUP_TITLE_KEYS: Partial<Record<PresetGroupKind, TranslationKey>> = {
  role: 'presetGroupRole',
  format: 'presetGroupFormat',
  style: 'presetGroupStyle',
  nsfw: 'presetGroupNsfw',
  engine: 'presetGroupEngine',
  think: 'presetGroupThink',
  doc: 'presetGroupDoc',
  meta: 'presetGroupMeta',
  review: 'presetGroupReview',
  core: 'presetGroupCore',
  logic: 'presetGroupLogic',
  fix: 'presetGroupFix',
  world: 'presetGroupWorld',
  dialog: 'presetGroupDialog',
  ext: 'presetGroupExt',
  'no-close': 'presetGroupNoClose',
  deprecated: 'presetGroupDeprecated',
  general: 'presetGroupGeneral',
}

/** 预设条目开关：按分类分组，单选组互斥；也可以看全部条目。 */
export const PresetSwitchesDialog = ({
  preset,
  onClose,
  onUpdated,
}: {
  preset: Preset
  onClose: () => void
  onUpdated: (preset: Preset) => void
}) => {
  const t = useTranslate()
  const [tab, setTab] = useState<'groups' | 'all'>('groups')
  const [busy, setBusy] = useState(false)
  const layout = useMemo(() => groupPresetBlocks(preset.blocks), [preset.blocks])
  // 猫神系预设把菜单分组写在块内容里：有 catgod 分组时只展示这些菜单项
  const menuGroups = useMemo(
    () => layout.groups.filter((group) => group.key.startsWith('catgod:')),
    [layout]
  )
  const menuEnabled = menuGroups.reduce((sum, group) => sum + group.enabled, 0)
  const menuTotal = menuGroups.reduce((sum, group) => sum + group.total, 0)
  const shownGroups = menuGroups.length > 0 ? menuGroups : layout.groups

  /** 单选组里每条条目对应的兄弟条目：启用自己时一并关闭别人 */
  const siblingsByIndex = useMemo(() => {
    const map = new Map<number, PresetEntry[]>()
    for (const group of layout.groups) {
      if (group.single) {
        for (const entry of group.entries) {
          map.set(
            entry.index,
            group.entries.filter((other) => other.index !== entry.index)
          )
        }
      }
      for (const single of group.singles) {
        for (const entry of single.entries) {
          map.set(
            entry.index,
            single.entries.filter((other) => other.index !== entry.index)
          )
        }
      }
    }
    return map
  }, [layout])

  const groupTitle = (group: PresetEntryGroup): string => {
    const key = GROUP_TITLE_KEYS[group.kind]
    if (key) {
      return t(key)
    }
    return group.title || t('presetGroupOther')
  }

  const positionLabel = (entry: PresetEntry): string => {
    if (entry.position === 'tail') {
      return t('lorePresetSwitchesPosTail')
    }
    if (entry.position === 'depth') {
      return t('lorePresetSwitchesPosDepth', { depth: entry.depth })
    }
    return ''
  }

  const toggle = async (entry: PresetEntry) => {
    if (busy) {
      return
    }
    const next = !entry.enabled
    const siblings = next ? siblingsByIndex.get(entry.index) ?? [] : []
    const changes = [
      ...siblings
        .filter((sibling) => sibling.enabled)
        .map((sibling) => ({ index: sibling.index, enabled: false })),
      { index: entry.index, enabled: next },
    ]
    setBusy(true)
    try {
      onUpdated(await setPresetBlockStates(preset.id, changes))
    } catch (err) {
      showToast(
        t('loreActionFailed', {
          message: err instanceof Error ? err.message : String(err),
        })
      )
    } finally {
      setBusy(false)
    }
  }

  const renderChip = (entry: PresetEntry) => {
    const pos = positionLabel(entry)
    return (
      <button
        key={entry.index}
        type="button"
        role="switch"
        aria-checked={entry.enabled}
        aria-label={t('lorePresetSwitchesToggle', { name: entry.name })}
        title={entry.name}
        disabled={busy}
        className={clsx(
          styles.switchChip,
          entry.enabled ? styles.switchChipOn : styles.switchChipOff
        )}
        onClick={() => void toggle(entry)}
      >
        <span className={styles.switchChipText}>{entry.label || entry.name}</span>
        {pos ? <span className={styles.switchChipPos}>{pos}</span> : null}
      </button>
    )
  }

  const body =
    layout.total === 0 ? (
      <div className={styles.empty}>{t('lorePresetSwitchesEmpty')}</div>
    ) : tab === 'all' ? (
      <div className={styles.switchChips}>{layout.all.map(renderChip)}</div>
    ) : (
      shownGroups.map((group) => (
        <div className={styles.switchGroup} key={group.key}>
          <div className={styles.switchGroupHead}>
            {group.icon ? <span className={styles.switchGroupIcon}>{group.icon}</span> : null}
            <span className={styles.switchGroupTitle}>{groupTitle(group)}</span>
            {group.single ? (
              <span className={styles.switchBadge}>{t('lorePresetSwitchesSingle')}</span>
            ) : null}
            <span className={styles.switchCount}>
              {group.enabled}/{group.total}
            </span>
          </div>
          {group.entries.length > 0 ? (
            <div className={styles.switchChips}>{group.entries.map(renderChip)}</div>
          ) : null}
          {group.singles.map((single) => (
            <div className={styles.switchSingle} key={single.key}>
              <div className={styles.switchSingleHead}>
                <span className={styles.switchSingleTitle}>{single.title}</span>
                <span className={styles.switchBadge}>{t('lorePresetSwitchesSingle')}</span>
                <span className={styles.switchCount}>
                  {single.entries.filter((entry) => entry.enabled).length}/{single.entries.length}
                </span>
              </div>
              <div className={styles.switchChips}>{single.entries.map(renderChip)}</div>
            </div>
          ))}
        </div>
      ))
    )

  return (
    <Modal
      title={t('lorePresetSwitchesTitle', { name: preset.name || preset.id })}
      closable
      className={styles.switchDialog}
      mainClassName={styles.switchScroll}
      onClose={onClose}
      footer={
        <div className={styles.editorActions}>
          <Button variant="outlined" size="m" onClick={onClose}>
            {t('close')}
          </Button>
        </div>
      }
    >
      <div className={styles.switchSummary}>
        <span>{t('lorePresetSwitchesEnabled', { on: layout.enabled, total: layout.total })}</span>
        <span className={styles.switchHint}>{t('lorePresetSwitchesHint')}</span>
      </div>
      <div className={styles.switchTabs} role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'groups'}
          className={clsx(styles.switchTab, tab === 'groups' && styles.switchTabActive)}
          onClick={() => setTab('groups')}
        >
          {menuGroups.length > 0 ? t('lorePresetSwitchesTabMenu') : t('lorePresetSwitchesTabGroups')}
          {menuGroups.length > 0 ? ` ${menuEnabled}/${menuTotal}` : ''}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'all'}
          className={clsx(styles.switchTab, tab === 'all' && styles.switchTabActive)}
          onClick={() => setTab('all')}
        >
          {t('lorePresetSwitchesTabAll')} {layout.total}
        </button>
      </div>
      {body}
    </Modal>
  )
}
