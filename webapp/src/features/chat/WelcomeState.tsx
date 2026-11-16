import { Button } from '../../components/base/Button.js'
import { IconNewChatOutline20 } from '../../components/base/icons.js'
import { useChatStore } from '../../stores/chatStore.js'
import { useSessionStore } from '../../stores/sessionStore.js'
import styles from './WelcomeState.module.css'

/** 未选中会话时的欢迎页，提供新建会话入口 */
export const WelcomeState = () => {
  const create = useSessionStore((state) => state.create)
  const openSession = useChatStore((state) => state.openSession)

  const handleCreate = async () => {
    const session = await create('')
    await openSession(session.id)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.logo}>FlyAI</div>
      <h2 className={styles.title}>开始一段新的对话</h2>
      <p className={styles.hint}>从左侧选择历史会话，或直接新建一个会话</p>
      <Button onClick={() => void handleCreate()}>
        <span className={styles.buttonIcon}>
          <IconNewChatOutline20 />
        </span>
        新建会话
      </Button>
    </div>
  )
}
