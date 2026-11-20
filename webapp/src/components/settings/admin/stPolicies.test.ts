import { describe, expect, it } from 'vitest'
import {
  bytesToMbText,
  formatDomainList,
  mbTextToBytes,
  parseDomainList,
} from './stPolicies.js'

describe('parseDomainList', () => {
  it('按换行/逗号切分并归一化', () => {
    expect(parseDomainList('Example.COM\n.jsdelivr.net, cdn.foo.org，bar.io; baz.dev')).toEqual([
      'example.com',
      'jsdelivr.net',
      'cdn.foo.org',
      'bar.io',
      'baz.dev',
    ])
  })

  it('去重并丢弃含路径的项', () => {
    expect(parseDomainList('a.com a.com https://b.com/x c.com/path')).toEqual(['a.com'])
  })

  it('空输入得到空列表', () => {
    expect(parseDomainList('  \n , ')).toEqual([])
  })
})

describe('domain list formatting', () => {
  it('回显为每行一个域名', () => {
    expect(formatDomainList(['a.com', 'b.com'])).toBe('a.com\nb.com')
    expect(formatDomainList(undefined)).toBe('')
    expect(formatDomainList([1, 'a.com'])).toBe('a.com')
  })
})

describe('bytes/MB conversion', () => {
  it('字节转 MB 文本', () => {
    expect(bytesToMbText(5 * 1024 * 1024)).toBe('5')
    expect(bytesToMbText(1536 * 1024)).toBe('1.5')
    expect(bytesToMbText(undefined)).toBe('5')
  })

  it('MB 文本转字节', () => {
    expect(mbTextToBytes('5')).toBe(5 * 1024 * 1024)
    expect(mbTextToBytes('0.5')).toBe(512 * 1024)
  })
})
