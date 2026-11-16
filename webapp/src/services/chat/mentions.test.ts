import { describe, expect, it } from 'vitest'
import { parseMentionTargets } from './mentions.js'

const members = [
  { id: 'm1', name: '座位一', aliases: ['小爱同学', '小爱'] },
  { id: 'm2', name: '座位二', aliases: ['阿绫'] },
]

describe('parseMentionTargets', () => {
  it('按出现顺序解析 @ 提及并去重', () => {
    expect(parseMentionTargets('@阿绫 你好，@小爱同学 也来一下', members)).toEqual(['m2', 'm1'])
    expect(parseMentionTargets('@小爱 @小爱 @小爱', members)).toEqual(['m1'])
  })

  it('长名优先，短名不截断长名匹配', () => {
    expect(parseMentionTargets('@小爱同学出来', members)).toEqual(['m1'])
  })

  it('座位名作为兜底候选', () => {
    expect(parseMentionTargets('@座位二 干活', members)).toEqual(['m2'])
  })

  it('无提及/空成员返回空数组', () => {
    expect(parseMentionTargets('普通文本', members)).toEqual([])
    expect(parseMentionTargets('@小爱', [])).toEqual([])
    expect(parseMentionTargets('', members)).toEqual([])
  })

  it('正则元字符在名字里不破坏匹配', () => {
    const weird = [{ id: 'w1', name: 'C++(测试)' }]
    expect(parseMentionTargets('@C++(测试) 冲', weird)).toEqual(['w1'])
  })
})
