import { describe, expect, it } from 'vitest'
import {
  estimateBase64Bytes,
  scaledDimensions,
  splitDataUrl,
} from './image.js'

describe('scaledDimensions', () => {
  it('小图不放大', () => {
    expect(scaledDimensions(800, 600)).toEqual({ width: 800, height: 600 })
  })

  it('超宽图按最长边等比缩小', () => {
    expect(scaledDimensions(3136, 1568)).toEqual({ width: 1568, height: 784 })
  })

  it('超高图同理且结果至少 1px', () => {
    expect(scaledDimensions(100, 4000)).toEqual({ width: 39, height: 1568 })
    expect(scaledDimensions(0, 0)).toEqual({ width: 1, height: 1 })
  })
})

describe('splitDataUrl', () => {
  it('拆出 media_type 与无前缀 base64', () => {
    expect(splitDataUrl('data:image/jpeg;base64,QUJD')).toEqual({
      media_type: 'image/jpeg',
      data: 'QUJD',
    })
  })

  it('非 data URL 兜底为 octet-stream 且整体作为 data', () => {
    expect(splitDataUrl('rawbase64')).toEqual({
      media_type: 'application/octet-stream',
      data: 'rawbase64',
    })
  })
})

describe('estimateBase64Bytes', () => {
  it('按 4/3 估算字节数', () => {
    expect(estimateBase64Bytes('QUJD')).toBe(3)
    expect(estimateBase64Bytes('QUJDLw')).toBe(4)
  })
})
