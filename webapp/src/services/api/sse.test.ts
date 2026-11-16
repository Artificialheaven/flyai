import { describe, expect, it } from 'vitest'
import { ServerSentEventDecoder } from './sse.js'

describe('ServerSentEventDecoder', () => {
  it('解析完整事件并记录 event/id/data', () => {
    const decoder = new ServerSentEventDecoder()
    const events = decoder.push(
      'event: content_delta\nid: 7\ndata: {"text":"你好"}\n\n'
    )
    expect(events).toHaveLength(1)
    expect(events[0]).toEqual({
      event: 'content_delta',
      data: '{"text":"你好"}',
      lastEventId: '7',
    })
  })

  it('支持 CRLF、注释行与缺省 event 名', () => {
    const decoder = new ServerSentEventDecoder()
    const events = decoder.push(': keep-alive\r\ndata: {"a":1}\r\n\r\n')
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('message')
    expect(events[0].data).toBe('{"a":1}')
  })

  it('分片到达时缓存半包，多行 data 以 \\n 连接', () => {
    const decoder = new ServerSentEventDecoder()
    expect(decoder.push('event: done\nda')).toEqual([])
    expect(decoder.push('ta: {"ok":\nda')).toEqual([])
    const events = decoder.push('ta: true}\n\n')
    expect(events).toHaveLength(1)
    expect(events[0].data).toBe('{"ok":\ntrue}')
  })

  it('end 时丢弃没有空行收尾的不完整事件', () => {
    const decoder = new ServerSentEventDecoder()
    decoder.push('data: {"orphan":1}')
    expect(decoder.end()).toEqual([])
  })

  it('连续两个事件分别派发且 id 持续更新', () => {
    const decoder = new ServerSentEventDecoder()
    const events = decoder.push('id: 1\ndata: 1\n\nid: 2\ndata: 2\n\n')
    expect(events.map((e) => e.lastEventId)).toEqual(['1', '2'])
  })
})
