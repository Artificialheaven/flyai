import { describe, expect, it } from 'vitest'
import type { PresetBlock } from '../flyai/types.js'
import { groupPresetBlocks, presetHeading } from './presetBlocks.js'

const block = (
  name: string,
  enabled = true,
  content = 'x',
  identifier = ''
): PresetBlock => ({
  name,
  content,
  enabled,
  role: 'system',
  position: 'main',
  depth: 4,
  insert_order: 100,
  identifier,
})

const names = (entries: { name: string }[]) => entries.map((entry) => entry.name)

describe('presetHeading', () => {
  it('识别 ----分类---- 分隔块', () => {
    expect(presetHeading(block('----角色控制----'))).toBe('角色控制')
    expect(presetHeading(block('====== 文风 ======'))).toBe('文风')
  })

  it('普通块不当作分隔块', () => {
    expect(presetHeading(block('情绪别太激烈'))).toBe('')
    expect(presetHeading(block('📏<game>标签（别关）'))).toBe('')
  })

  it('名称为空时按内容首行识别', () => {
    expect(presetHeading(block('', true, '----额外设定----\n正文'))).toBe('额外设定')
  })
})

describe('groupPresetBlocks', () => {
  it('按猫神预设的命名约定分组并识别单选组', () => {
    const layout = groupPresetBlocks([
      block('用户角色'),
      block('📏<game>标签（别关）'),
      block('⚙️防媚User', false),
      block('😸情绪别太激烈'),
      block('😸活人感加强'),
      block('❤️nsfw风格|正常'),
      block('❤️nsfw风格|更舒缓', false),
      block('❤️nsfw风格|色色！', false),
      block('📝文风（选一）|五感', false),
      block('📝文风（选一）丨s喵特调一'),
      block('（必看）免责声明'),
      block('（不要关）召唤术'),
      block('----角色控制----'),
      block('----格式要求----'),
      block('要转述', false),
      block('不要转述', false),
      block('----额外设定----'),
      block('📏行动选项'),
    ])

    // 分隔块只做标题，不计入条目
    expect(layout.total).toBe(15)
    const byKind = new Map(layout.groups.map((group) => [group.kind, group]))

    expect(names(byKind.get('role')!.entries)).toEqual(['😸情绪别太激烈', '😸活人感加强'])
    expect(names(byKind.get('format')!.entries)).toEqual([
      '📏<game>标签（别关）',
      '📏行动选项',
    ])
    expect(names(byKind.get('engine')!.entries)).toEqual(['⚙️防媚User'])
    expect(byKind.get('engine')!.enabled).toBe(0)
    expect(names(byKind.get('doc')!.entries)).toEqual(['（必看）免责声明'])
    expect(names(byKind.get('no-close')!.entries)).toEqual(['（不要关）召唤术'])

    const nsfw = byKind.get('nsfw')!
    expect(nsfw.singles).toHaveLength(1)
    expect(nsfw.singles[0].title).toBe('nsfw风格')
    expect(names(nsfw.singles[0].entries)).toEqual([
      '❤️nsfw风格|正常',
      '❤️nsfw风格|更舒缓',
      '❤️nsfw风格|色色！',
    ])
    expect(nsfw.singles[0].entries.map((entry) => entry.label)).toEqual([
      '正常',
      '更舒缓',
      '色色！',
    ])
    expect(nsfw.total).toBe(3)
    expect(nsfw.enabled).toBe(1)

    const style = byKind.get('style')!
    expect(style.singles[0].title).toBe('文风')
    expect(style.singles[0].entries.map((entry) => entry.label)).toEqual([
      '五感',
      's喵特调一',
    ])

    // 要X / 不要X 归成互斥组，标题取公共部分
    const section = layout.groups.find((group) => group.title === '格式要求')!
    expect(section.singles[0].title).toBe('转述')
    expect(names(section.singles[0].entries)).toEqual(['要转述', '不要转述'])
    expect(section.singles[0].entries[0].enabled).toBe(false)
  })

  it('优先使用猫神 catgodml 菜单标记分组', () => {
    const layout = groupPresetBlocks([
      block('用户角色'),
      block('😸情绪别太激烈', true, '<<<|catgodml|menu:角色控制:😸情绪别太激烈>>>正文'),
      block('😸活人感加强', true, '<<<|catgodml|menu:角色控制:😸活人感加强>>>正文'),
      block('要转述', false, '<<<|catgodml|radio:玩家输入转述:要转述（扩写玩家行动）>>>正文'),
      block('不要转述', false, '<<<|catgodml|radio:玩家输入转述:不要转述（直接往后接）>>>正文'),
    ])
    expect(layout.total).toBe(5)
    // 猫神分组排在前面
    expect(layout.groups[0].title).toBe('角色控制')
    const role = layout.groups.find((group) => group.title === '角色控制')!
    expect(role.single).toBe(false)
    expect(role.entries.map((entry) => entry.label)).toEqual([
      '😸情绪别太激烈',
      '😸活人感加强',
    ])
    const radio = layout.groups.find((group) => group.title === '玩家输入转述')!
    expect(radio.single).toBe(true)
    expect(radio.entries.map((entry) => entry.label)).toEqual([
      '要转述（扩写玩家行动）',
      '不要转述（直接往后接）',
    ])
    expect(radio.enabled).toBe(0)
    expect(radio.total).toBe(2)
  })

  it('教程里的 catgod 占位标记不建组', () => {
    const layout = groupPresetBlocks([
      block(
        '📖自定义宏教程（魔改可看）',
        false,
        '<<<|catgodml|menu:分组名:显示名称>>>示例',
        'catgod-macro-tutorial-doc'
      ),
    ])
    expect(layout.groups.map((group) => group.title)).not.toContain('分组名')
    expect(layout.groups[0].kind).toBe('doc')
  })

  it('无分类标记的条目按最近的分隔块归组，否则进通用条目', () => {
    const layout = groupPresetBlocks([
      block('开场白'),
      block('----额外设定----'),
      block('字数要求'),
    ])
    expect(layout.groups.map((group) => group.kind)).toEqual(['general', 'section'])
    expect(layout.groups[0].title).toBe('')
    expect(layout.groups[1].title).toBe('额外设定')
    expect(names(layout.groups[1].entries)).toEqual(['字数要求'])
  })

  it('括号前缀的废弃条目单独归组', () => {
    const layout = groupPresetBlocks([
      block('（废弃无效）📘COT核查清单·精简档'),
      block('🛡️（废弃）强破限'),
      block('（必看）免责声明'),
    ])
    const kinds = layout.groups.map((group) => group.kind)
    expect(kinds).toContain('deprecated')
    expect(kinds).toContain('doc')
    const deprecated = layout.groups.find((group) => group.kind === 'deprecated')!
    expect(names(deprecated.entries)).toEqual([
      '（废弃无效）📘COT核查清单·精简档',
      '🛡️（废弃）强破限',
    ])
  })

  it('全部条目保持原始顺序，单选组条目用短名', () => {
    const layout = groupPresetBlocks([
      block('⚙️防抢话'),
      block('❤️nsfw风格|正常'),
      block('❤️nsfw风格|更舒缓'),
      block('📝文风（选一）|五感'),
    ])
    expect(layout.all.map((entry) => entry.index)).toEqual([0, 1, 2, 3])
    // 最后一条「选一」只有一条，不成单选组，保留原名
    expect(layout.all.map((entry) => entry.label)).toEqual([
      '防抢话',
      '正常',
      '更舒缓',
      '文风（选一）|五感',
    ])
    expect(layout.enabled).toBe(4)
  })

  it('单条「选一」不拆单选组，保留原名', () => {
    const layout = groupPresetBlocks([block('📝文风（选一）|五感')])
    const style = layout.groups[0]
    expect(style.singles).toHaveLength(0)
    expect(names(style.entries)).toEqual(['📝文风（选一）|五感'])
  })

  it('空名空内容块被跳过，内容首行兜底为名称', () => {
    const layout = groupPresetBlocks([
      block('', true, ''),
      block('', true, '第一行标题\n第二行'),
    ])
    expect(layout.total).toBe(1)
    expect(layout.all[0].name).toBe('第一行标题')
  })
})
