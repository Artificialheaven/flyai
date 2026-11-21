import type { PresetBlock } from '../flyai/types.js'

/**
 * 预设 prompt 块 → 可分组的开关清单。
 *
 * 酒馆预设的 prompt 块没有官方分类字段。猫神系预设把菜单分组写进块内容：
 * `<<<|catgodml|menu:分组:显示名>>>` / `<<<|catgodml|radio:分组:显示名>>>`
 * （radio 即单选组），这是权威来源，优先使用。
 *
 * 没有标记的预设再按命名约定做启发式解析：
 * - `----分类----` 这类纯分隔块是分组标题（本身不作为开关条目）；
 * - 名称前缀的 emoji / 括号标签（⚙️📝❤️📏😸、（必看）、【别关】）表示分类；
 * - `分类|变体`、`（选一）`、`要X / 不要X` 表示互斥的单选组。
 */

export type PresetGroupKind =
  | 'role'
  | 'format'
  | 'style'
  | 'nsfw'
  | 'engine'
  | 'think'
  | 'doc'
  | 'meta'
  | 'review'
  | 'core'
  | 'logic'
  | 'fix'
  | 'world'
  | 'dialog'
  | 'ext'
  | 'no-close'
  | 'deprecated'
  | 'general'
  | 'section'
  | 'other'

export type PresetEntry = {
  /** 原始 blocks 下标，开关接口按它定位 */
  index: number
  /** 原始条目名 */
  name: string
  /** 分组里显示的短名（去掉分类前缀 / 单选前缀） */
  label: string
  enabled: boolean
  position: PresetBlock['position']
  depth: number
  identifier: string
}

export type PresetSingleGroup = {
  key: string
  title: string
  entries: PresetEntry[]
}

export type PresetEntryGroup = {
  key: string
  kind: PresetGroupKind
  /** 分类名；kind 为 section/other 时直接展示 */
  title: string
  icon: string
  /** 整组互斥（猫神 radio 组）：同时只保留一条 */
  single: boolean
  /** 多选条目 */
  entries: PresetEntry[]
  /** 组内互斥的单选组 */
  singles: PresetSingleGroup[]
  enabled: number
  total: number
}

export type PresetSwitchLayout = {
  groups: PresetEntryGroup[]
  /** 全部条目（原始顺序，单选组里的条目按短名展示） */
  all: PresetEntry[]
  enabled: number
  total: number
}

const HEADING_RE = /^[-=_*~#]{2,}\s*(.+?)\s*[-=_*~#]{2,}$/u
/** 猫神菜单标记：<<<|catgodml|menu:分组:显示名>>> / radio 版为单选组 */
const CATGOD_MARKER_RE =
  /<<<\|\s*catgodml\s*\|\s*(menu|radio)\s*[:：]\s*([^:：>|]+?)\s*[:：]\s*([^>|]+?)\s*>>>/u
const BRACKET_RE = /^[（(【\[]\s*([^）)】\]]{1,10})\s*[）)】\]]/u
const EMOJI_RE = /^(\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*)/u
const SINGLE_SPLIT_RE = /^(.+?)\s*[|｜丨]\s*(.+)$/u
const SELECT_TAG_RE = /[（(]\s*(?:选一|单选)\s*[）)]/gu
const OPPOSITE_RE = /^(要|不要|禁止|开启|关闭)(.+)$/u

const EMOJI_KINDS: Record<string, PresetGroupKind> = {
  '😸': 'role',
  '🎭': 'role',
  '📏': 'format',
  '📝': 'style',
  '🎨': 'style',
  '❤': 'nsfw',
  '🔞': 'nsfw',
  '⛔': 'nsfw',
  '⚙': 'engine',
  '🧩': 'engine',
  '🧠': 'think',
  '📖': 'doc',
  '📚': 'doc',
  '📘': 'meta',
  '🧪': 'review',
  '🛡': 'core',
  '🧭': 'logic',
  '🔧': 'fix',
  '🌍': 'world',
  '🗣': 'dialog',
}

const BRACKET_KINDS: Record<string, PresetGroupKind> = {
  必看: 'doc',
  不要关: 'no-close',
  别关: 'no-close',
  勿删: 'no-close',
  废弃无效: 'deprecated',
  选一: 'other',
  单选: 'other',
}

const normalizeMarker = (marker: string): string => marker.replace(/️/gu, '')
const stripSelectTag = (text: string): string => text.replace(SELECT_TAG_RE, '').trim()

/** 纯分隔块（`----角色控制----`）的分组标题；不是分隔块返回空串。 */
export const presetHeading = (block: PresetBlock): string => {
  const name = (block.name || '').trim()
  const byName = name.match(HEADING_RE)
  if (byName) {
    return byName[1].trim()
  }
  if (name) {
    return ''
  }
  const firstLine = (block.content || '').trim().split('\n')[0].trim()
  const byContent = firstLine.match(HEADING_RE)
  return byContent ? byContent[1].trim() : ''
}

type ParsedMarker = {
  marker: string
  /** 分组图标：只有 emoji 前缀才当图标，括号标签不算 */
  icon: string
  label: string
  kind: PresetGroupKind
  fallback: string
}

const parseMarker = (name: string): ParsedMarker => {
  const text = name.trim()
  const bracket = text.match(BRACKET_RE)
  if (bracket) {
    const tag = bracket[1].trim()
    return {
      marker: bracket[0],
      icon: '',
      label: text.slice(bracket[0].length).trim(),
      kind: BRACKET_KINDS[tag] ?? 'other',
      fallback: tag,
    }
  }
  const emoji = text.match(EMOJI_RE)
  if (emoji) {
    const marker = emoji[1]
    return {
      marker,
      icon: normalizeMarker(marker),
      label: text
        .slice(marker.length)
        .replace(/^[|｜丨]+/u, '')
        .trim(),
      kind: EMOJI_KINDS[normalizeMarker(marker)] ?? 'other',
      fallback: marker,
    }
  }
  return { marker: '', icon: '', label: text, kind: 'general', fallback: '' }
}

/** 预设里的协议/配置块：占位符包裹与扩展配置不参与条目开关。 */
const isProtocolBlock = (block: PresetBlock, name: string): boolean => {
  if (block.identifier === 'SPresetSettings') {
    return true
  }
  const trimmed = name.trim().toLowerCase()
  return trimmed === 'char' || trimmed === '/char'
}

/** 单选组：`分类|变体`、`（选一）`、`要X/不要X` 三类约定。 */
const parseSingle = (label: string): { base: string; rest: string } | null => {
  const split = label.match(SINGLE_SPLIT_RE)
  if (split) {
    const base = stripSelectTag(split[1])
    if (base && split[2].trim()) {
      return { base, rest: split[2].trim() }
    }
  }
  if (/选一|单选/u.test(label)) {
    const base = stripSelectTag(label)
    return base && base !== label ? { base, rest: label } : null
  }
  const opposite = label.match(OPPOSITE_RE)
  if (opposite) {
    return { base: `__opposite__:${opposite[2].trim()}`, rest: label }
  }
  return null
}

type CatgodMarker = { kind: 'menu' | 'radio'; group: string; label: string }

/** 猫神菜单标记；教程里的占位示例（分组名/显示名称）不算实际条目。 */
const catgodMarker = (block: PresetBlock): CatgodMarker | null => {
  if (block.identifier === 'catgod-macro-tutorial-doc') {
    return null
  }
  const match = (block.content || '').match(CATGOD_MARKER_RE)
  if (!match) {
    return null
  }
  const group = match[2].trim()
  const label = match[3].trim()
  if (!group || !label || group === '分组名') {
    return null
  }
  return { kind: match[1] === 'radio' ? 'radio' : 'menu', group, label }
}

const entryName = (block: PresetBlock, index: number): string => {
  const name = (block.name || '').trim()
  if (name) {
    return name
  }
  const firstLine = (block.content || '').trim().split('\n')[0].trim()
  return firstLine.slice(0, 24) || `#${index + 1}`
}

export const groupPresetBlocks = (blocks: PresetBlock[]): PresetSwitchLayout => {
  const groups: PresetEntryGroup[] = []
  const groupsByKey = new Map<string, PresetEntryGroup>()
  const all: PresetEntry[] = []
  let section = ''

  const groupFor = (
    key: string,
    kind: PresetGroupKind,
    title: string,
    icon: string
  ): PresetEntryGroup => {
    const found = groupsByKey.get(key)
    if (found) {
      return found
    }
    const group: PresetEntryGroup = {
      key,
      kind,
      title,
      icon,
      single: false,
      entries: [],
      singles: [],
      enabled: 0,
      total: 0,
    }
    groupsByKey.set(key, group)
    groups.push(group)
    return group
  }

  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index]
    const heading = presetHeading(block)
    if (heading) {
      section = heading
      continue
    }
    if (!(block.name || '').trim() && !(block.content || '').trim()) {
      continue
    }
    const name = entryName(block, index)
    if (isProtocolBlock(block, name)) {
      continue
    }
    const catgod = catgodMarker(block)
    if (catgod) {
      const entry: PresetEntry = {
        index,
        name: catgod.label,
        label: catgod.label,
        enabled: Boolean(block.enabled),
        position: block.position,
        depth: block.depth,
        identifier: block.identifier,
      }
      all.push(entry)
      const group = groupFor(`catgod:${catgod.group}`, 'section', catgod.group, '')
      if (catgod.kind === 'radio') {
        group.single = true
      }
      group.entries.push(entry)
      continue
    }
    const marker = parseMarker(name)
    let kind: PresetGroupKind = marker.kind
    let title = ''
    let icon = marker.icon
    let key: string = kind
    if (kind === 'general') {
      if (section) {
        kind = 'section'
        title = section
        icon = ''
        key = `section:${section}`
      } else {
        key = 'general'
      }
    } else if (kind === 'other') {
      key = `other:${marker.fallback}`
      title = marker.fallback
      // 未知 emoji 只当分组标题显示，避免图标与标题重复
      icon = ''
    }
    if (kind !== 'deprecated' && /废弃/u.test(name)) {
      kind = 'deprecated'
      key = 'deprecated'
      title = ''
    }
    const entry: PresetEntry = {
      index,
      name,
      label: marker.label || name,
      enabled: Boolean(block.enabled),
      position: block.position,
      depth: block.depth,
      identifier: block.identifier,
    }
    all.push(entry)
    groupFor(key, kind, title, icon).entries.push(entry)
  }

  // 组内再拆单选组：同一 base 至少两条才算互斥组，否则退回普通条目。
  for (const group of groups) {
    if (group.key.startsWith('catgod:')) {
      // 猫神 radio 组由 group.single 标记，不需要再按命名猜
      group.entries.sort((a, b) => a.index - b.index)
      group.total = group.entries.length
      group.enabled = group.entries.filter((entry) => entry.enabled).length
      continue
    }
    const kept: PresetEntry[] = []
    const candidates = new Map<string, { title: string; entries: PresetEntry[] }>()
    for (const entry of group.entries) {
      const single = parseSingle(entry.label)
      if (!single) {
        kept.push(entry)
        continue
      }
      const title = single.base.startsWith('__opposite__:')
        ? single.base.slice('__opposite__:'.length)
        : single.base
      const bucket = candidates.get(single.base)
      if (bucket) {
        bucket.entries.push({ ...entry, label: single.rest })
      } else {
        candidates.set(single.base, {
          title,
          entries: [{ ...entry, label: single.rest }],
        })
      }
    }
    for (const [base, bucket] of candidates) {
      if (bucket.entries.length >= 2) {
        group.singles.push({ key: `${group.key}:${base}`, title: bucket.title, entries: bucket.entries })
      } else {
        // 只有一条带「选一」/「要X」：不算互斥组，保留原名
        const only = bucket.entries[0]
        kept.push({ ...only, label: only.name })
      }
    }
    kept.sort((a, b) => a.index - b.index)
    group.entries = kept
    group.singles.sort(
      (a, b) => Math.min(...a.entries.map((e) => e.index)) - Math.min(...b.entries.map((e) => e.index))
    )
    group.total =
      group.entries.length + group.singles.reduce((sum, single) => sum + single.entries.length, 0)
    group.enabled =
      group.entries.filter((entry) => entry.enabled).length +
      group.singles.reduce(
        (sum, single) => sum + single.entries.filter((entry) => entry.enabled).length,
        0
      )
  }

  // 单选组里的条目也要在「全部条目」里显示短名
  const shortLabels = new Map<number, string>()
  for (const group of groups) {
    for (const single of group.singles) {
      for (const entry of single.entries) {
        shortLabels.set(entry.index, entry.label)
      }
    }
  }
  const allEntries = all.map((entry) =>
    shortLabels.has(entry.index) ? { ...entry, label: shortLabels.get(entry.index) ?? entry.label } : entry
  )

  // 猫神菜单分组排在最前（保持出现顺序），其余启发式分组在后
  const ordered = groups
    .filter((group) => group.total > 0)
    .sort((a, b) => {
      const rank = (group: PresetEntryGroup) => (group.key.startsWith('catgod:') ? 0 : 1)
      return rank(a) - rank(b)
    })

  return {
    groups: ordered,
    all: allEntries,
    enabled: allEntries.filter((entry) => entry.enabled).length,
    total: allEntries.length,
  }
}
