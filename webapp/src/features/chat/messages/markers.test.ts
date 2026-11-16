import { describe, expect, it } from 'vitest'
import {
  buildPixivReveal,
  extractAudioMeta,
  extractGeneratedAudio,
  extractGeneratedImage,
  extractImageMeta,
  isAudioMarkerText,
  isImageMarkerText,
  isPixivCandidatesText,
  isPixivPicksText,
  parsePixivCands,
  parsePixivPicks,
} from './markers.js'

describe('图片标记', () => {
  const output = [
    '[图片已生成] /uploads/gen_1.jpg',
    '[图片参数] {"engine":"sd","prompt":"cat","negative_prompt":"lowres","steps":28,"seed":-1,"width":512,"height":768}',
    '图片已成功生成。不要输出链接。',
  ].join('\n')

  it('提取 URL 与参数', () => {
    expect(extractGeneratedImage(output)).toBe('/uploads/gen_1.jpg')
    expect(extractImageMeta(output)).toEqual({
      engine: 'sd',
      prompt: 'cat',
      negative_prompt: 'lowres',
      steps: 28,
      seed: -1,
      width: 512,
      height: 768,
    })
  })

  it('识别图片首行标记', () => {
    expect(isImageMarkerText(output)).toBe(true)
    expect(isImageMarkerText('[图片] /x.png')).toBe(true)
    expect(isImageMarkerText('图片已生成')).toBe(false)
  })

  it('非法 JSON 参数返回 null', () => {
    expect(extractImageMeta('[图片参数] {oops}')).toBeNull()
  })
})

describe('语音标记', () => {
  const output = [
    '[语音已生成] /uploads/tts_1.mp3',
    '[语音参数] {"speaker":"S_abc","format":"mp3"}',
    '已经为你朗读啦',
  ].join('\n')

  it('提取 URL 与参数', () => {
    expect(extractGeneratedAudio(output)).toBe('/uploads/tts_1.mp3')
    expect(extractAudioMeta(output)).toEqual({ speaker: 'S_abc', format: 'mp3' })
  })

  it('识别语音首行标记', () => {
    expect(isAudioMarkerText(output)).toBe(true)
    expect(isAudioMarkerText('[语音] /x.wav')).toBe(true)
    expect(isAudioMarkerText('[语音参数] {}')).toBe(false)
  })
})

describe('Pixiv 候选与精选', () => {
  const search = [
    '[P站候选]',
    '[候] 2 111 https://i.pximg.net/a.jpg',
    '[候] 1 222 https://i.pximg.net/b.jpg',
    '请用 pick_pixiv 选择',
  ].join('\n')
  const picks = ['[P站精选]', '[选] 1 222 /uploads/pick_222.jpg'].join('\n')

  it('解析候选与精选行', () => {
    expect(parsePixivCands(search)).toEqual([
      { index: 2, pid: '111', url: 'https://i.pximg.net/a.jpg' },
      { index: 1, pid: '222', url: 'https://i.pximg.net/b.jpg' },
    ])
    expect(parsePixivPicks(picks)).toEqual([{ index: 1, pid: '222', url: '/uploads/pick_222.jpg' }])
  })

  it('识别首行标记', () => {
    expect(isPixivCandidatesText(search)).toBe(true)
    expect(isPixivPicksText(picks)).toBe(true)
    expect(isPixivPicksText(search)).toBe(false)
  })

  it('按顺序把精选原图回填到最近一次搜索', () => {
    const reveal = buildPixivReveal([
      { id: 'call-search-1', name: 'search_pixiv', content: search },
      { id: 'call-pick-1', name: 'pick_pixiv', content: picks },
      { id: 'call-search-2', name: 'search_pixiv', content: '[P站候选]\n[候] 3 333 /x.jpg' },
      { id: 'call-pick-2', name: 'pick_pixiv', content: '[P站精选]\n[选] 3 333 /uploads/pick_333.jpg' },
    ])
    expect(reveal).toEqual({
      'call-search-1': { 1: '/uploads/pick_222.jpg' },
      'call-search-2': { 3: '/uploads/pick_333.jpg' },
    })
  })

  it('没有搜索前导的精选不影响映射', () => {
    expect(buildPixivReveal([{ id: 'pick', name: 'pick_pixiv', content: picks }])).toEqual({})
  })
})