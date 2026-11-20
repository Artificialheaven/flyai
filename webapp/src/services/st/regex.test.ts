import { describe, expect, it } from 'vitest'
import type { RegexScript } from '../flyai/types.js'
import {
  applyDisplayScripts,
  applyDisplayScriptsToUser,
  applyRegexScripts,
  compileRegexScripts,
  renderVariableMacros,
  splitRegexLiteral,
  stripUpdateBlocks,
} from './regex.js'

const compile = (scripts: RegexScript[]) => compileRegexScripts(scripts, 'display')

describe('splitRegexLiteral', () => {
  it('拆分 /pattern/flags 字面量', () => {
    expect(splitRegexLiteral('/abc/gi')).toEqual({ source: 'abc', flags: 'gi' })
    expect(splitRegexLiteral('/a\\/b/g')).toEqual({ source: 'a\\/b', flags: 'g' })
  })

  it('裸字符串按全局替换', () => {
    expect(splitRegexLiteral('abc')).toEqual({ source: 'abc', flags: 'g' })
  })

  it('不支持的标志回退为整串裸模式', () => {
    expect(splitRegexLiteral('/abc/x')).toEqual({ source: '/abc/x', flags: 'g' })
  })
})

describe('compileRegexScripts', () => {
  it('非数组与空数组返回空', () => {
    expect(compileRegexScripts(undefined, 'display')).toEqual([])
    expect(compileRegexScripts('nope', 'display')).toEqual([])
    expect(compileRegexScripts([], 'display')).toEqual([])
  })

  it('跳过 disabled 与无效正则', () => {
    const compiled = compile([
      { findRegex: 'a', replaceString: 'b', disabled: true },
      { findRegex: '/[unclosed/', replaceString: 'x' },
      { findRegex: 'c', replaceString: 'd' },
    ])
    expect(compiled).toHaveLength(1)
    expect(compiled[0].replace).toBe('d')
  })

  it('展示侧跳过 promptOnly，prompt 侧跳过 markdownOnly', () => {
    const scripts: RegexScript[] = [
      { findRegex: 'a', replaceString: 'b', promptOnly: true },
      { findRegex: 'c', replaceString: 'd', markdownOnly: true },
    ]
    expect(compileRegexScripts(scripts, 'display')).toHaveLength(1)
    expect(compileRegexScripts(scripts, 'prompt')).toHaveLength(1)
  })

  it('placement 默认 AI 消息，非 1/2 的值被过滤', () => {
    const [defaulted] = compile([{ findRegex: 'a', replaceString: 'b' }])
    expect(defaulted.placement).toEqual([2])
    const [filtered] = compile([
      { findRegex: 'a', replaceString: 'b', placement: [1, 3] as unknown as number[] },
    ])
    expect(filtered.placement).toEqual([1])
  })

  it('深度 0/负数视为无限制', () => {
    const [script] = compile([
      { findRegex: 'a', replaceString: 'b', minDepth: 0, maxDepth: -1 },
    ])
    expect(script.minDepth).toBeNull()
    expect(script.maxDepth).toBeNull()
  })
})

describe('applyRegexScripts', () => {
  it('按 placement 过滤', () => {
    const scripts = compile([
      { findRegex: 'a', replaceString: 'b', placement: 2 },
    ])
    expect(applyRegexScripts('a', scripts, { placement: 1 })).toBe('a')
    expect(applyRegexScripts('a', scripts, { placement: 2 })).toBe('b')
  })

  it('按 minDepth/maxDepth 过滤', () => {
    const scripts = compile([
      { findRegex: 'a', replaceString: 'b', minDepth: 2, maxDepth: 3 },
    ])
    expect(applyRegexScripts('a', scripts, { placement: 2, depth: 1 })).toBe('a')
    expect(applyRegexScripts('a', scripts, { placement: 2, depth: 2 })).toBe('b')
    expect(applyRegexScripts('a', scripts, { placement: 2, depth: 4 })).toBe('a')
  })

  it('展开 $1/$&/$<name>/$$', () => {
    const scripts = compile([
      {
        findRegex: '(?<word>a)(b)',
        replaceString: '$2-$1-$<word>-$&-$$',
      },
    ])
    expect(applyRegexScripts('ab', scripts, { placement: 2 })).toBe('b-a-a-ab-$')
  })

  it('trimStrings 从捕获组中剔除', () => {
    const scripts = compile([
      { findRegex: '\\[(x)\\]', replaceString: '$1', trimStrings: ['x'] },
    ])
    expect(applyRegexScripts('[x]', scripts, { placement: 2 })).toBe('')
  })

  it('多次调用不残留 lastIndex', () => {
    const scripts = compile([{ findRegex: '/a/g', replaceString: 'b' }])
    expect(applyRegexScripts('aa', scripts, { placement: 2 })).toBe('bb')
    expect(applyRegexScripts('aa', scripts, { placement: 2 })).toBe('bb')
  })
})

describe('renderVariableMacros', () => {
  const variables = {
    stat_data: {
      属性: { 体力: 12 },
      物品: ['符纸', '桃木剑'],
      标志: true,
      空值: null,
      对象: { a: 1 },
    },
  }

  it('按路径取值并格式化', () => {
    expect(renderVariableMacros('{{format_message_variable::stat_data.属性.体力}}', variables)).toBe(
      '12'
    )
    expect(renderVariableMacros('{{get_message_variable::stat_data.物品[1]}}', variables)).toBe(
      '桃木剑'
    )
    expect(renderVariableMacros('{{format_message_variable::stat_data.标志}}', variables)).toBe(
      'true'
    )
    expect(renderVariableMacros('{{format_message_variable::stat_data.对象}}', variables)).toBe(
      '{"a":1}'
    )
  })

  it('缺失字段与 null 展开为空串', () => {
    expect(renderVariableMacros('x{{format_message_variable::stat_data.缺失}}', variables)).toBe('x')
    expect(renderVariableMacros('x{{format_message_variable::stat_data.空值}}', variables)).toBe('x')
  })

  it('无变量数据时原样返回', () => {
    expect(renderVariableMacros('{{format_message_variable::stat_data.属性}}')).toBe(
      '{{format_message_variable::stat_data.属性}}'
    )
  })
})

describe('stripUpdateBlocks', () => {
  it('剥离更新块并保留其余文本', () => {
    expect(
      stripUpdateBlocks('前<UpdateVariable><JSONPatch>[]</JSONPatch></UpdateVariable>后')
    ).toBe('前后')
  })

  it('只剩空白时返回空串', () => {
    expect(stripUpdateBlocks('<UpdateVariable><JSONPatch>[]</JSONPatch></UpdateVariable> ')).toBe('')
    expect(stripUpdateBlocks('')).toBe('')
  })
})

describe('applyDisplayScripts', () => {
  it('卡内脚本把更新块美化成 details 时不再二次剥离', () => {
    const out = applyDisplayScripts(
      '正文\n<UpdateVariable><JSONPatch>[]</JSONPatch></UpdateVariable>',
      [
        {
          findRegex: '<UpdateVariable[^>]*>([\\s\\S]*?)</UpdateVariable>',
          replaceString: '<details>$1</details>',
          placement: 2,
        },
      ]
    )
    expect(out).toBe('正文\n<details><JSONPatch>[]</JSONPatch></details>')
  })

  it('无对应脚本时兜底剥离更新块', () => {
    expect(
      applyDisplayScripts(
        '正文\n<UpdateVariable><JSONPatch>[]</JSONPatch></UpdateVariable>',
        []
      )
    ).toBe('正文\n')
  })

  it('脚本执行后再展开变量宏', () => {
    const out = applyDisplayScripts(
      '体力={{format_message_variable::stat_data.体力}}',
      [],
      undefined,
      { stat_data: { 体力: 9 } }
    )
    expect(out).toBe('体力=9')
  })

  it('用户消息入口只应用 placement=1 的脚本', () => {
    const scripts: RegexScript[] = [
      { findRegex: 'a', replaceString: 'b', placement: 1 },
      { findRegex: 'a', replaceString: 'c', placement: 2 },
    ]
    expect(applyDisplayScriptsToUser('a', scripts)).toBe('b')
  })
})
