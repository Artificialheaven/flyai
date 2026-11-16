/**
 * 图片附件处理（对照旧版 images.js）：
 * 超 8MB 拒收；最长边 1568 等比缩放；透明通道铺白底后统一转 JPEG q0.82；
 * 输出后端聊天接口要求的 {media_type, data}（data 为无 data: 前缀的 base64）。
 */
import type { ChatImagePayload } from '../services/chat/stream.js'

export const IMAGE_MAX_COUNT = 4
export const IMAGE_MAX_EDGE = 1568
export const IMAGE_QUALITY = 0.82
export const IMAGE_MAX_FILE = 8 * 1024 * 1024

export class ImageProcessError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ImageProcessError'
  }
}

export const isImageFile = (file: File): boolean => file.type.startsWith('image/')

/** 纯函数：等比缩放后的整数尺寸，最小 1px */
export const scaledDimensions = (
  width: number,
  height: number,
  maxEdge: number = IMAGE_MAX_EDGE
): { width: number; height: number } => {
  const scale = Math.min(1, maxEdge / Math.max(width, height))
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

/** 拆分 data URL；返回后端载荷格式（无前缀 base64 + mediaType） */
export const splitDataUrl = (dataUrl: string): ChatImagePayload => {
  const comma = dataUrl.indexOf(',')
  const head = comma >= 0 ? dataUrl.slice(0, comma) : ''
  const mediaMatch = /^data:([^;]+)/.exec(head)
  return {
    media_type: mediaMatch ? mediaMatch[1] : 'application/octet-stream',
    data: comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl,
  }
}

/** base64 文本对应的近似字节数，用于配额/超限提示 */
export const estimateBase64Bytes = (base64: string): number =>
  Math.floor((base64.length * 3) / 4)

const readAsDataUrl = (file: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new ImageProcessError('读取失败'))
    reader.readAsDataURL(file)
  })

const loadImage = (url: string): Promise<HTMLImageElement> =>
  new Promise((resolve, reject) => {
    const pic = new Image()
    pic.onload = () => resolve(pic)
    pic.onerror = () => reject(new ImageProcessError('无法解析为图片'))
    pic.src = url
  })

/** 单张图片：校验 → 缩放 → 白底 JPEG → base64 载荷 */
export const processImageFile = async (file: File): Promise<ChatImagePayload> => {
  if (file.size > IMAGE_MAX_FILE) {
    throw new ImageProcessError('文件超过 8MB')
  }
  const sourceUrl = await readAsDataUrl(file)
  const pic = await loadImage(sourceUrl)
  const { width, height } = scaledDimensions(pic.naturalWidth || pic.width, pic.naturalHeight || pic.height)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    throw new ImageProcessError('压缩失败')
  }
  // 透明 PNG 转 JPEG 前铺白底
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, width, height)
  ctx.drawImage(pic, 0, 0, width, height)
  const jpegUrl = canvas.toDataURL('image/jpeg', IMAGE_QUALITY)
  return splitDataUrl(jpegUrl)
}

/** 渲染用：把后端载荷还原为 data URL */
export const imageToDataUrl = (img: ChatImagePayload): string =>
  `data:${img.media_type};base64,${img.data}`
