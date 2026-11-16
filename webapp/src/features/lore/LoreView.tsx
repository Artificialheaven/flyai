import { useEffect, useMemo, useRef, useState } from 'react'
import { Avatar } from '../../components/base/Avatar.js'
import { Button } from '../../components/base/Button.js'
import { IconButton } from '../../components/base/IconButton.js'
import { Menu, type MenuOption } from '../../components/base/Menu.js'
import { Modal } from '../../components/base/Modal.js'
import { showToast } from '../../components/base/toastState.js'
import {
  IconDownloadOutline16,
  IconEditOutline16,
  IconEllipsisOutline16,
  IconGlobeLanguageOutline14,
  IconMenuOutline20,
  IconTrashOutline16,
} from '../../components/base/icons.js'
import {
  downloadJson,
  exportCharacter,
  exportWorld,
  generateCharacterFromText,
  importCharacter,
  importCharacterCard,
  importWorld,
  importWorldbook,
} from '../../services/api/lore.js'
import { useAuth } from '../../stores/auth.js'
import {
  cardDisplayName,
  rootCards,
  useLoreStore,
  useOwnCardCheck,
  variantCards,
} from '../../stores/loreStore.js'
import type { Character, World } from '../../types/lore.js'
import { CharacterEditor } from './editor/CharacterEditor.js'
import { WorldEditor } from './editor/WorldEditor.js'
import styles from './LoreView.module.css'

type CharEditorState =
  | { mode: 'create'; initial?: Character; parentId?: string }
  | { mode: 'edit'; initial: Character }

type WorldEditorState =
  | { mode: 'create' }
  | { mode: 'edit'; initial: World }

type DeleteTarget = {
  kind: 'character' | 'world'
  id: string
  name: string
  root: boolean
}

/** 酒馆 CCv2/v3 判定，与旧版 looksLikeTavernCard 一致 */
const looksLikeTavernCard = (obj: Record<string, unknown>): boolean => {
  if (typeof obj.spec === 'string' && obj.spec.startsWith('chara_card')) {
    return true
  }
  const data = obj.data
  if (
    data &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    ('description' in data || 'first_mes' in data || 'character_book' in data) &&
    !('table' in data)
  ) {
    return true
  }
  return (
    !obj.type &&
    !('table' in obj) &&
    'description' in obj &&
    ('mes_example' in obj || 'first_mes' in obj)
  )
}

/** 导入体归一：FlyAI 导出信封原样传，裸对象包一层 data（后端只认 {type,data}） */
const toImportBody = (obj: Record<string, unknown>): { type?: string; data: Record<string, unknown> } => {
  const data = obj.data
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    return {
      type: typeof obj.type === 'string' ? obj.type : undefined,
      data: data as Record<string, unknown>,
    }
  }
  return { data: obj }
}

/**
 * 内容库主视图：角色卡（根卡 → 副本两级导航）、世界观列表、导入导出、发布与 AI 生成。
 * 入口为 hash 路由 #/lore，编辑用宽弹窗覆盖（移动端全宽）。
 */
export const LoreView = ({
  onOpenSidebar,
  onBack,
}: {
  onOpenSidebar: () => void
  onBack: () => void
}) => {
  const characters = useLoreStore((state) => state.characters)
  const worlds = useLoreStore((state) => state.worlds)
  const loaded = useLoreStore((state) => state.loaded)
  const ensureLoaded = useLoreStore((state) => state.ensureLoaded)
  const refresh = useLoreStore((state) => state.refresh)
  const saveCharacter = useLoreStore((state) => state.saveCharacter)
  const removeCharacter = useLoreStore((state) => state.removeCharacter)
  const forkCharacter = useLoreStore((state) => state.forkCharacter)
  const publishCharacter = useLoreStore((state) => state.publishCharacter)
  const unpublishCharacter = useLoreStore((state) => state.unpublishCharacter)
  const saveWorld = useLoreStore((state) => state.saveWorld)
  const removeWorld = useLoreStore((state) => state.removeWorld)

  const user = useAuth((state) => state.user)
  const ownCard = useOwnCardCheck()
  const isAdmin = user?.role === 'admin'

  const [tab, setTab] = useState<'characters' | 'worlds'>('characters')
  const [navRootId, setNavRootId] = useState('')
  const [charEditor, setCharEditor] = useState<CharEditorState | null>(null)
  const [worldEditor, setWorldEditor] = useState<WorldEditorState | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null)
  const [forkTarget, setForkTarget] = useState<Character | null>(null)
  const [importing, setImporting] = useState(false)

  const [textImportOpen, setTextImportOpen] = useState(false)
  const [textImportText, setTextImportText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [textImportError, setTextImportError] = useState('')

  const fileRef = useRef<HTMLInputElement>(null)
  const importKindRef = useRef<'characters' | 'worlds'>('characters')

  useEffect(() => {
    void ensureLoaded()
  }, [ensureLoaded])

  const roots = useMemo(() => rootCards(characters), [characters])
  const navRoot = useMemo(
    () => (navRootId && characters.find((card) => card.id === navRootId)) || null,
    [characters, navRootId]
  )
  const navVariants = useMemo(
    () => (navRoot ? variantCards(characters, navRoot.id) : []),
    [characters, navRoot]
  )

  useEffect(() => {
    // 删除或数据刷新后根卡不存在时退回列表
    if (navRootId && loaded && !navRoot) {
      setNavRootId('')
    }
  }, [navRootId, navRoot, loaded])

  const handleSavedCharacter = (saved: Character) => {
    setCharEditor(null)
    setNavRootId(saved.parent_id || saved.id)
    showToast('已保存角色卡')
  }

  const handleSavedWorld = (_saved: World) => {
    setWorldEditor(null)
    showToast('已保存世界观')
  }

  const handleDelete = async () => {
    if (!deleteTarget) {
      return
    }
    const target = deleteTarget
    setDeleteTarget(null)
    try {
      if (target.kind === 'character') {
        await removeCharacter(target.id)
        if (target.root || navRootId === target.id) {
          setNavRootId('')
        }
      } else {
        await removeWorld(target.id)
      }
      showToast(`已删除「${target.name}」`)
    } catch (err) {
      showToast(`删除失败：${(err as Error).message}`, true)
    }
  }

  const handleFork = async () => {
    if (!forkTarget) {
      return
    }
    const target = forkTarget
    setForkTarget(null)
    try {
      const forked = await forkCharacter(target.id)
      setCharEditor({ mode: 'edit', initial: forked })
      showToast('已复制为你的角色卡，保存后生效')
    } catch (err) {
      showToast(`复制失败：${(err as Error).message}`, true)
    }
  }

  const exportCard = async (card: Character) => {
    try {
      downloadJson(await exportCharacter(card.id), `${cardDisplayName(card)}.json`)
    } catch (err) {
      showToast(`导出失败：${(err as Error).message}`, true)
    }
  }

  const exportWorldItem = async (world: World) => {
    try {
      downloadJson(await exportWorld(world.id), `${world.name || '世界观'}.json`)
    } catch (err) {
      showToast(`导出失败：${(err as Error).message}`, true)
    }
  }

  const togglePublish = async (card: Character) => {
    try {
      if (card.public) {
        await unpublishCharacter(card.id)
        showToast('已取消发布')
      } else {
        await publishCharacter(card.id)
        showToast(card.parent_id ? '已发布该副本（主卡同步发布）' : '已发布主卡及其全部副本')
      }
    } catch (err) {
      showToast(`操作失败：${(err as Error).message}`, true)
    }
  }

  const startImport = (kind: 'characters' | 'worlds') => {
    importKindRef.current = kind
    fileRef.current?.click()
  }

  const handleImportFile = async (file: File) => {
    const kind = importKindRef.current
    setImporting(true)
    try {
      const isPng =
        kind === 'characters' && (/\.png$/i.test(file.name) || file.type.startsWith('image/png'))
      if (isPng) {
        await importCharacterCard(file)
        await refresh()
        setNavRootId('')
        showToast(`导入成功：${file.name}`)
        return
      }

      const text = await file.text()
      if (kind === 'worlds') {
        const head = text.slice(0, 4096)
        if (/"entries"\s*:/.test(head) && !/"table"\s*:/.test(head)) {
          const result = await importWorldbook(file)
          await refresh()
          const stats = result.stats || {}
          showToast(
            `世界书导入成功：${stats.entries ?? 0} 条${
              stats.depth ? `（深度常驻 ${stats.depth}）` : ''
            }`
          )
          return
        }
      }

      let parsed: unknown
      try {
        parsed = JSON.parse(text)
      } catch {
        showToast('JSON 解析失败', true)
        return
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        showToast('文件内容不是有效的导入对象', true)
        return
      }
      const obj = parsed as Record<string, unknown>

      if (kind === 'characters' && looksLikeTavernCard(obj)) {
        await importCharacterCard(file)
        await refresh()
        setNavRootId('')
        showToast(`导入成功：${file.name}`)
        return
      }

      if (kind === 'characters') {
        await importCharacter(toImportBody(obj))
        setNavRootId('')
      } else {
        await importWorld(toImportBody(obj))
      }
      await refresh()
      showToast(`导入成功：${file.name}`)
    } catch (err) {
      showToast(`导入失败：${(err as Error).message}`, true)
    } finally {
      setImporting(false)
      if (fileRef.current) {
        fileRef.current.value = ''
      }
    }
  }

  const parseCharacterText = async () => {
    const text = textImportText.trim()
    if (!text) {
      setTextImportError('请先粘贴文本')
      return
    }
    setParsing(true)
    setTextImportError('')
    try {
      const parsed = await generateCharacterFromText(text)
      if (!parsed?.name) {
        throw new Error('返回数据缺少角色名')
      }
      setTextImportOpen(false)
      setTextImportText('')
      setCharEditor({ mode: 'create', initial: parsed })
      showToast('解析完成，请核对后保存')
    } catch (err) {
      setTextImportError(`解析失败：${(err as Error).message}`)
    } finally {
      setParsing(false)
    }
  }

  const renderCharacterList = () => {
    if (navRoot) {
      const rootOwn = ownCard(navRoot)
      return (
        <>
          <div className={styles.detailBar}>
            <button type="button" className={styles.backButton} onClick={() => setNavRootId('')}>
              ← 返回根角色卡
            </button>
            <span className={styles.detailHint}>
              「{navRoot.name || '未命名'}」的主卡与副本
            </span>
          </div>
          <div className={styles.variantList}>
            <VariantRow
              card={navRoot}
              root
              own={rootOwn}
              isAdmin={isAdmin}
              onEdit={() => setCharEditor({ mode: 'edit', initial: navRoot })}
              onFork={() => setForkTarget(navRoot)}
              onExport={() => void exportCard(navRoot)}
              onPublish={() => void togglePublish(navRoot)}
              onDelete={() =>
                setDeleteTarget({ kind: 'character', id: navRoot.id, name: cardDisplayName(navRoot), root: true })
              }
            />
            {navVariants.map((variant) => (
              <VariantRow
                key={variant.id}
                card={variant}
                own={ownCard(variant)}
                isAdmin={isAdmin}
                onEdit={() => setCharEditor({ mode: 'edit', initial: variant })}
                onFork={() => setForkTarget(variant)}
                onExport={() => void exportCard(variant)}
                onPublish={() => void togglePublish(variant)}
                onDelete={() =>
                  setDeleteTarget({
                    kind: 'character',
                    id: variant.id,
                    name: cardDisplayName(variant),
                    root: false,
                  })
                }
              />
            ))}
          </div>
          {rootOwn ? (
            <button
              type="button"
              className={styles.smallButton}
              onClick={() => setCharEditor({ mode: 'create', parentId: navRoot.id, initial: navRoot })}
            >
              ＋ 新建副本
            </button>
          ) : (
            <p className={styles.hint}>共享主卡不能直接新建副本；可先复制为我的角色卡。</p>
          )}
        </>
      )
    }

    return (
      <>
        <div className={styles.rootGrid}>
          {roots.map((card) => {
            const count = variantCards(characters, card.id).length
            return (
              <button
                type="button"
                key={card.id}
                className={styles.rootCard}
                onClick={() => setNavRootId(card.id)}
              >
                <Avatar src={card.avatar} name={card.name} size={48} />
                <span className={styles.rootName}>{card.name || '未命名'}</span>
                <span className={styles.rootMeta}>
                  {card.public ? '🌐 ' : ''}
                  {count} 个副本
                </span>
              </button>
            )
          })}
          {roots.length === 0 && loaded ? (
            <div className={styles.emptyTile}>暂无角色卡，点击「新建根角色卡」开始</div>
          ) : null}
          <button
            type="button"
            className={`${styles.rootCard} ${styles.addTile}`}
            onClick={() => setCharEditor({ mode: 'create' })}
          >
            <span className={styles.addIcon}>＋</span>
            <span className={styles.rootName}>新建根角色卡</span>
          </button>
        </div>
        <div className={styles.toolbar}>
          <Button size="s" variant="outlined" disabled={importing} onClick={() => startImport('characters')}>
            ⇪ 导入角色
          </Button>
          <Button size="s" variant="outlined" onClick={() => setTextImportOpen(true)}>
            ✨ 从文本导入
          </Button>
          {!loaded ? <span className={styles.hint}>加载中…</span> : null}
        </div>
      </>
    )
  }

  const renderWorldList = () => (
    <>
      <div className={styles.worldList}>
        {worlds.map((world) => (
          <div key={world.id} className={styles.worldRow}>
            <Avatar src={world.avatar} name={world.name} size={40} />
            <div className={styles.worldInfo}>
              <span className={styles.worldName}>{world.name || '未命名'}</span>
              <span className={styles.worldMeta}>{(world.table || []).length} 条内容</span>
            </div>
            <div className={styles.rowButtons}>
              <button
                type="button"
                className={styles.smallButton}
                onClick={() => setWorldEditor({ mode: 'edit', initial: world })}
              >
                编辑
              </button>
              <button type="button" className={styles.smallButton} onClick={() => void exportWorldItem(world)}>
                导出
              </button>
              <button
                type="button"
                className={`${styles.smallButton} ${styles.dangerButton}`}
                onClick={() =>
                  setDeleteTarget({ kind: 'world', id: world.id, name: world.name || '未命名', root: false })
                }
              >
                删除
              </button>
            </div>
          </div>
        ))}
        {worlds.length === 0 && loaded ? <div className={styles.emptyTile}>暂无世界观</div> : null}
      </div>
      <div className={styles.toolbar}>
        <Button size="s" variant="outlined" onClick={() => setWorldEditor({ mode: 'create' })}>
          ＋ 新建世界观
        </Button>
        <Button size="s" variant="outlined" disabled={importing} onClick={() => startImport('worlds')}>
          ⇪ 导入世界书 / 世界观
        </Button>
      </div>
    </>
  )

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <IconButton
          size="m"
          icon={<IconMenuOutline20 />}
          tooltip="打开会话列表"
          className={styles.menuButton}
          onClick={onOpenSidebar}
        />
        <div className={styles.titleBlock}>
          <h1 className={styles.title}>内容库</h1>
          <p className={styles.subtitle}>管理角色卡与世界观，会话中可选择绑定</p>
        </div>
        <Button size="s" variant="outlined" onClick={onBack}>
          返回聊天
        </Button>
      </header>

      <div className={styles.body}>
        <div className={styles.tabs}>
          <button
            type="button"
            className={`${styles.tab} ${tab === 'characters' ? styles.tabActive : ''}`}
            onClick={() => setTab('characters')}
          >
            角色卡 <span className={styles.tabCount}>{characters.length}</span>
          </button>
          <button
            type="button"
            className={`${styles.tab} ${tab === 'worlds' ? styles.tabActive : ''}`}
            onClick={() => setTab('worlds')}
          >
            世界观 <span className={styles.tabCount}>{worlds.length}</span>
          </button>
        </div>

        <div className={styles.content}>
          {tab === 'characters' ? renderCharacterList() : renderWorldList()}
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json,image/png"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) {
            void handleImportFile(file)
          } else if (fileRef.current) {
            fileRef.current.value = ''
          }
        }}
      />

      {charEditor ? (
        <CharacterEditor
          mode={charEditor.mode}
          initial={charEditor.initial}
          parentId={charEditor.mode === 'create' ? charEditor.parentId : ''}
          save={(payload) =>
            saveCharacter(payload, charEditor.mode === 'edit' ? charEditor.initial.id : undefined)
          }
          onClose={() => setCharEditor(null)}
          onSaved={handleSavedCharacter}
        />
      ) : null}

      {worldEditor ? (
        <WorldEditor
          mode={worldEditor.mode}
          initial={worldEditor.mode === 'edit' ? worldEditor.initial : undefined}
          save={(payload) =>
            saveWorld(payload, worldEditor.mode === 'edit' ? worldEditor.initial.id : undefined)
          }
          onClose={() => setWorldEditor(null)}
          onSaved={handleSavedWorld}
        />
      ) : null}

      {textImportOpen ? (
        <Modal
          title="✨ 从文本导入角色"
          closable
          className={styles.textDialog}
          mainClassName={styles.textMain}
          onClose={() => (parsing ? undefined : setTextImportOpen(false))}
          footer={
            <>
              <Button variant="outlined" size="m" disabled={parsing} onClick={() => setTextImportOpen(false)}>
                取消
              </Button>
              <Button size="m" disabled={parsing} onClick={() => void parseCharacterText()}>
                {parsing ? '解析中…' : '✨ 开始解析'}
              </Button>
            </>
          }
        >
          <p className={styles.hint}>
            粘贴角色档案、百科片段或任意设定文本，AI 会提取名称、简介、经历、开场白、对话范例、历史后指令与内容表。解析后打开编辑器，<b>点击保存才会真正创建</b>。原文上限 2 万字，会消耗少量模型额度。
          </p>
          <textarea
            className={styles.textarea}
            rows={12}
            value={textImportText}
            placeholder="把角色相关的文本粘贴到这里……"
            onChange={(event) => setTextImportText(event.target.value)}
          />
          {textImportError ? <div className={styles.error}>{textImportError}</div> : null}
        </Modal>
      ) : null}

      {deleteTarget ? (
        <Modal
          title={deleteTarget.kind === 'character' ? '删除角色卡' : '删除世界观'}
          closable
          onClose={() => setDeleteTarget(null)}
          footer={
            <>
              <Button size="m" variant="outlined" onClick={() => setDeleteTarget(null)}>
                取消
              </Button>
              <Button size="m" variant="danger" onClick={() => void handleDelete()}>
                删除
              </Button>
            </>
          }
        >
          <p className={styles.confirmText}>
            {deleteTarget.kind === 'character' && deleteTarget.root
              ? `确定删除根角色卡「${deleteTarget.name}」？其全部副本也会一并删除，且无法恢复。`
              : `确定删除「${deleteTarget.name}」吗？此操作无法恢复。`}
          </p>
        </Modal>
      ) : null}

      {forkTarget ? (
        <Modal
          title="共享角色卡"
          closable
          onClose={() => setForkTarget(null)}
          footer={
            <>
              <Button size="m" variant="outlined" onClick={() => setForkTarget(null)}>
                取消
              </Button>
              <Button size="m" onClick={() => void handleFork()}>
                复制并编辑
              </Button>
            </>
          }
        >
          <p className={styles.confirmText}>共享卡不能直接修改。是否复制为我的角色卡并编辑？</p>
        </Modal>
      ) : null}
    </div>
  )
}

/** 主卡 / 副本行：左侧归属信息，右侧编辑、导出、发布与删除 */
const VariantRow = ({
  card,
  root = false,
  own,
  isAdmin,
  onEdit,
  onFork,
  onExport,
  onPublish,
  onDelete,
}: {
  card: Character
  root?: boolean
  own: boolean
  isAdmin: boolean
  onEdit: () => void
  onFork: () => void
  onExport: () => void
  onPublish: () => void
  onDelete: () => void
}) => {
  // 发布仅管理员可操作；删除仅自己的卡（共享卡后端 403）
  const options: MenuOption[] = []
  if (own && isAdmin) {
    options.push({
      key: 'publish',
      label: card.public ? '取消发布' : '发布到市场',
      icon: <IconGlobeLanguageOutline14 />,
      onSelect: onPublish,
    })
  }
  if (own) {
    options.push({
      key: 'delete',
      label: '删除',
      icon: <IconTrashOutline16 />,
      danger: true,
      onSelect: onDelete,
    })
  }

  return (
    <div className={styles.variantRow}>
      <span className={`${styles.variantTag} ${root ? '' : styles.variantTagChild}`}>
        {root ? '主卡' : '副本'}
      </span>
      <Avatar src={card.avatar} name={card.name} size={34} />
      <span className={styles.variantName}>{cardDisplayName(card)}</span>
      {card.public ? (
        <span className={styles.publicTag} title="已共享到公共卡池">
          <IconGlobeLanguageOutline14 />
        </span>
      ) : null}
      <div className={styles.rowButtons}>
        <button
          type="button"
          className={styles.iconTextButton}
          title={own ? '编辑' : '复制为我的卡并编辑'}
          onClick={own ? onEdit : onFork}
        >
          <IconEditOutline16 />
          编辑
        </button>
        <button type="button" className={styles.iconTextButton} title="导出为 JSON" onClick={onExport}>
          <IconDownloadOutline16 />
          导出
        </button>
        {options.length > 0 ? (
          <Menu options={options}>
            {({ toggle }) => (
              <IconButton
                size="xs"
                icon={<IconEllipsisOutline16 />}
                tooltip="更多操作"
                onClick={toggle}
              />
            )}
          </Menu>
        ) : null}
      </div>
    </div>
  )
}