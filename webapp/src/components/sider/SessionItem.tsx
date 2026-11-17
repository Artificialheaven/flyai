import clsx from 'clsx'
import { memo, useState } from 'react'
import { translate, useTranslate } from '../../i18n/index.js'
import {
  IconCheckOutline16,
  IconDownloadOutline16,
  IconEditOutline16,
  IconEllipsisOutline16,
  IconRightUpOutline14,
  IconTrashOutline16,
} from '../base/icons.js'
import {
  pinSession,
  renameSession,
  selectSession,
} from '../../services/chat/store.js'
import { FlyApiError } from '../../services/flyai/client.js'
import {
  exportSessionJson,
  exportSessionMarkdown,
  exportSessionZip,
} from '../../services/flyai/transfer.js'
import type { SessionSummary } from '../../services/chat/types.js'
import { IconButton } from '../base/IconButton.js'
import { Menu } from '../base/Menu.js'
import { showToast } from '../base/toastState.js'
import { closeMobileSider } from './mobileSiderState.js'
import styles from './SessionItem.module.css'

const runExport = (task: Promise<void>) => {
  void task.catch((error: unknown) => {
    const message =
      error instanceof FlyApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : translate('unknownError')
    showToast({ key: 'exportSessionFailed', params: { message }, type: 'error' })
  })
}

export const SessionItem = memo(function SessionItem({
  session,
  active,
  onDelete,
  snippet,
  matchCount,
  selecting = false,
  selected = false,
  onToggleSelect,
  onOpen,
}: {
  session: SessionSummary
  active: boolean
  onDelete: () => void
  /** 搜索命中片段（搜索模式） */
  snippet?: string
  matchCount?: number
  /** 批量选择模式：点击切换选中而不是跳转 */
  selecting?: boolean
  selected?: boolean
  onToggleSelect?: () => void
  /** 打开会话后的附加动作（如退出搜索） */
  onOpen?: () => void
}) {
  const t = useTranslate()
  const [editing, setEditing] = useState(false)
  const displayTitle = session.title || t('untitledChat')

  if (editing) {
    const finish = (title: string) => {
      setEditing(false)
      if (title.trim() && title.trim() !== displayTitle) {
        renameSession(session.id, title.trim())
      }
    }
    return (
      <input
        className={styles.renameInput}
        defaultValue={displayTitle}
        autoFocus
        onFocus={(event) => event.target.select()}
        onBlur={(event) => finish(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
            finish(event.currentTarget.value)
          } else if (event.key === 'Escape') {
            setEditing(false)
          }
        }}
      />
    )
  }

  return (
    <div
      className={clsx(
        styles.item,
        active && styles.active,
        selecting && selected && styles.selected,
        snippet !== undefined && styles.withSubtitle
      )}
      onClick={() => {
        if (selecting) {
          onToggleSelect?.()
          return
        }
        selectSession(session.id)
        onOpen?.()
        closeMobileSider()
      }}
    >
      {selecting ? (
        <span className={clsx(styles.check, selected && styles.checkOn)}>
          {selected ? <IconCheckOutline16 /> : null}
        </span>
      ) : null}
      <div className={styles.text}>
        <div className={styles.title}>{displayTitle}</div>
        {snippet !== undefined ? <div className={styles.subtitle}>{snippet}</div> : null}
      </div>
      {matchCount !== undefined ? (
        <span className={styles.match}>{t('searchSessionsMatchCount', { count: matchCount })}</span>
      ) : null}
      {selecting ? null : (
        <div className={styles.action} onClick={(event) => event.stopPropagation()}>
          <Menu
            options={[
              {
                key: 'rename',
                label: t('sessionItemMenuRenameOption'),
                icon: <IconEditOutline16 />,
                onSelect: () => setEditing(true),
              },
              {
                key: 'pin',
                label: session.pinned
                  ? t('sessionItemMenuUnpinOption')
                  : t('sessionItemMenuPinOption'),
                icon: <IconRightUpOutline14 />,
                onSelect: () => pinSession(session.id, !session.pinned),
              },
              {
                key: 'export-json',
                label: t('exportJsonLabel'),
                icon: <IconDownloadOutline16 />,
                onSelect: () => runExport(exportSessionJson(session.id, displayTitle)),
              },
              {
                key: 'export-markdown',
                label: t('exportMarkdownLabel'),
                icon: <IconDownloadOutline16 />,
                onSelect: () => runExport(exportSessionMarkdown(session.id, displayTitle)),
              },
              {
                key: 'export-zip',
                label: t('exportZipLabel'),
                icon: <IconDownloadOutline16 />,
                onSelect: () => runExport(exportSessionZip(session.id, displayTitle)),
              },
              {
                key: 'delete',
                label: t('delete'),
                icon: <IconTrashOutline16 />,
                danger: true,
                onSelect: onDelete,
              },
            ]}
          >
            {({ toggle }) => (
              <IconButton icon={<IconEllipsisOutline16 />} aria-label={t('more')} onClick={toggle} />
            )}
          </Menu>
        </div>
      )}
    </div>
  )
})