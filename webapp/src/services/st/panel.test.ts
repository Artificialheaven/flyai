import { describe, expect, it } from 'vitest'
import type { RegexScript } from '../flyai/types.js'
import { renderPanel, statDataRows, withoutAuthorRemovals } from './panel.js'

const PANEL_SCRIPTS: RegexScript[] = [
  {
    scriptName: '状态面板',
    findRegex: '<Panel/>',
    replaceString:
      '<div class="obs-card"><span>体力</span>{{format_message_variable::stat_data.角色.体力}}</div>',
    placement: [2],
    markdownOnly: true,
  },
]

describe('renderPanel', () => {
  it('把占位符替换成面板 HTML 并展开变量宏', () => {
    const out = renderPanel('<Panel/>', PANEL_SCRIPTS, {
      stat_data: { 角色: { 体力: 12 } },
    })
    expect(out).toContain('obs-card')
    expect(out).toContain('体力</span>12')
    expect(out).not.toContain('{{')
  })

  it('兼容直接传 stat_data 内容（无外层 stat_data 键）', () => {
    const out = renderPanel('<Panel/>', PANEL_SCRIPTS, { 角色: { 体力: 7 } })
    expect(out).toContain('7')
  })

  it('没有匹配脚本时原样返回占位符', () => {
    expect(renderPanel('<Panel/>', undefined, { stat_data: {} })).toBe('<Panel/>')
    expect(renderPanel('<Panel/>', [], undefined)).toBe('<Panel/>')
  })

  it('宏缺失的字段展开为空串', () => {
    const out = renderPanel('<Panel/>', PANEL_SCRIPTS, { stat_data: {} })
    expect(out).toContain('<span>体力</span></div>')
  })
})

describe('statDataRows', () => {
  it('对象递归成点路径，标量数组用顿号连接', () => {
    const rows = statDataRows({ 角色: { 体力: 12, 技能: ['剑', '盾'] } })
    expect(rows).toEqual([
      { key: '角色.体力', value: '12' },
      { key: '角色.技能', value: '剑、盾' },
    ])
  })

  it('对象数组按下标展开，空值与空数组给空串', () => {
    const rows = statDataRows({ 在场: [{ 名字: '甲' }, { 名字: '乙' }], 空: [], 无: null })
    expect(rows).toEqual([
      { key: '在场[0].名字', value: '甲' },
      { key: '在场[1].名字', value: '乙' },
      { key: '空', value: '' },
      { key: '无', value: '' },
    ])
  })

  it('截断超长字符串并按上限截断行数', () => {
    const rows = statDataRows({ 长: 'x'.repeat(600), a: 1, b: 2 }, 2)
    expect(rows).toHaveLength(2)
    expect(rows[0].value.endsWith('…')).toBe(true)
  })

  it('空对象给出占位行', () => {
    expect(statDataRows({})).toEqual([{ key: '—', value: '' }])
  })
})

describe('withoutAuthorRemovals', () => {
  it('过滤消息正文用的合成移除脚本，保留作者正则', () => {
    const scripts: RegexScript[] = [
      {
        scriptName: '移除',
        findRegex: '<Panel/>',
        replaceString: '',
        flyaiAuthorRemoval: true,
      },
      ...PANEL_SCRIPTS,
    ]
    const kept = withoutAuthorRemovals(scripts)
    expect(kept).toHaveLength(1)
    expect(kept?.[0].scriptName).toBe('状态面板')
    expect(withoutAuthorRemovals(undefined)).toBeUndefined()
  })
})
