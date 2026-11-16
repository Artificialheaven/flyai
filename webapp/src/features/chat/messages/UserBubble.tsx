import { useState } from 'react'
import { IconEditOutline16 } from '../../../components/base/icons.js'
import { Modal } from '../../../components/base/Modal.js'
import { useChatStore } from '../../../stores/chatStore.js'
import type { Message } from '../../../types/session.js'
import type { ViewerImage } from '../../images/ImageViewer.js'
import { MessageImages } from './MessageImages.js'
import styles from './Bubbles.module.css'

/** 用户气泡：右对齐；dm 下可编辑重发（后端仅 dm 支持 edit_message_id） */
export const UserBubble = ({
  message,
  canEdit,
  onOpenImage,
}: {
  message: Message
  canEdit: boolean
  onOpenImage: (image: ViewerImage) => void
}) => {
  const editMessage = useChatStore((state) => state.editMessage)
  const busy = useChatStore((state) => state.busy)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.content ?? '')

  return (
    <div className={styles.userRow}>
      <div className={styles.userBubble}>
        {message.images?.length ? <MessageImages images={message.images} onOpen={onOpenImage} /> : null}
        {message.content ? <div className={styles.userText}>{message.content}</div> : null}
        {canEdit ? (
          <button
            type="button"
            className={styles.inlineEdit}
            title="编辑这条消息"
            disabled={busy}
            onClick={() => {
              setDraft(message.content ?? '')
              setEditing(true)
            }}
          >
            <IconEditOutline16 />
          </button>
        ) : null}
      </div>
      {editing ? (
        <Modal
          title="编辑消息并重新发送"
          closable
          onClose={() => setEditing(false)}
          footer={
            <div className={styles.editFooter}>
              <button type="button" className={styles.editCancel} onClick={() => setEditing(false)}>
                取消
              </button>
              <button
                type="button"
                className={styles.editSend}
                disabled={!draft.trim() || busy}
                onClick={() => {
                  editMessage(message.id, draft)
                  setEditing(false)
                }}
              >
                重新发送
              </button>
            </div>
          }
        >
          <textarea
            className={styles.editArea}
            value={draft}
            rows={6}
            autoFocus
            onChange={(event) => setDraft(event.target.value)}
          />
        </Modal>
      ) : null}
    </div>
  )
}
