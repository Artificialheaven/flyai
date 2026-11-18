import { create } from 'zustand'
import type { LinkedMessage, TranslationMessage } from '../i18n/index.js'

/**
 * FlyAI 的图片附件在浏览器内直接转 base64 随 /api/chat 提交，不经 Files 服务：
 * 选中即视为就绪，serverId 复用本地 id（MessageFile / DraftAttachments 据此判断无上传中）。
 */
export const ACCEPTED_EXTS = ['png', 'jpg', 'jpeg', 'webp', 'gif']
/** 选择文件对话框的过滤条件 */
export const ACCEPT = ACCEPTED_EXTS.map((ext) => `.${ext}`).join(',')
/** 后端 /api/chat 的硬上限：单张 8MB、最多 4 张；GIF 不经压缩，仍以此为准 */
export const MAX_SIZE_MB = 8
export const MAX_COUNT = 4

const MB = 1024 * 1024
/** 自动压缩目标：超过该大小的非 GIF 图片在添加前会被压缩到 5MB 以内 */
export const COMPRESS_TARGET_MB = 5
/** 压缩后长边上限（像素），过大的原图先等比缩小 */
const COMPRESS_MAX_EDGE = 2048
/** 依次尝试的 JPEG 质量；当前缩放比例全部失败时再等比缩小重试 */
const JPEG_QUALITIES = [0.9, 0.8, 0.72, 0.62, 0.52]
const DOWNSCALE_STEP = 0.75
const MAX_COMPRESS_ROUNDS = 6
/** 压缩后统一改名 .jpg，扩展名与 MIME 保持一致 */
const COMPRESSED_EXTS_RE = /\.(png|jpe?g|webp)$/i

/** 文件扩展名（小写，不含点）；没有扩展名时为空 */
export const getFileExt = (name: string) => {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

export const isAcceptedExt = (name: string) => ACCEPTED_EXTS.includes(getFileExt(name))

/** GIF 会丢帧，不做重编码压缩，超限直接拒绝 */
const isGifFile = (file: File) => getFileExt(file.name) === 'gif'

/** 添加前校验：格式 → 大小 → 数量，任一不满足即整批不添加 */
export const validateFiles = (files: File[], currentCount: number): TranslationMessage | null => {
  if (files.some((file) => !isAcceptedExt(file.name))) {
    return { key: 'uploadUnsupportedFormat' }
  }
  // 非 GIF 图片超限会先自动压缩，只有不压缩的 GIF 才按 8MB 直接拒绝
  const oversizedGif = files.find((file) => isGifFile(file) && file.size > MAX_SIZE_MB * MB)
  if (oversizedGif) {
    return { key: 'uploadMaxMBSize', params: { size: MAX_SIZE_MB } }
  }
  if (currentCount + files.length > MAX_COUNT) {
    return { key: 'uploadMaxFiles', params: { count: MAX_COUNT } }
  }
  return null
}

/** 本地图片不会出现服务端错误；保留接口以兼容附件卡片 */
export const describeUploadError = (_error: unknown): TranslationMessage => ({
  key: 'uploadFileStatusFailed',
})

type NoticeListener = (notice: LinkedMessage) => void
const noticeListeners = new Set<NoticeListener>()

/** 订阅需要额外提示的上传失败，由界面层转成 toast；当前图片本地化后不会触发 */
export const onUploadNotice = (listener: NoticeListener) => {
  noticeListeners.add(listener)
  return () => {
    noticeListeners.delete(listener)
  }
}

/** 图片在本地解析，不存在配额上限 */
export const describeUploadQuota = (_error: unknown): LinkedMessage | undefined => undefined

/**
 * 任务按附件的本地 id 登记：图片选中即完成，serverId 即本地 id，
 * 发送时由 drafts 读取 blob 转 base64，不需要任何网络请求。
 */
export type UploadTask =
  | { status: 'uploading' }
  | { status: 'done'; serverId: string }
  | { status: 'error'; error: TranslationMessage; retryable: boolean }

type UploadState = { tasks: Record<string, UploadTask> }

export const useUploads = create<UploadState>(() => ({ tasks: {} }))

const setTask = (id: string, task: UploadTask | null) =>
  useUploads.setState((state) => ({
    tasks: task
      ? { ...state.tasks, [id]: task }
      : Object.fromEntries(Object.entries(state.tasks).filter(([key]) => key !== id)),
  }))

/** 立即完成并返回本地 id；保留异步签名，允许调用方等待 */
export const uploadAttachment = (
  id: string,
  _blob: Blob,
  _name: string,
  _isImage: boolean
): Promise<string> => {
  setTask(id, { status: 'done', serverId: id })
  return Promise.resolve(id)
}

export const cancelUpload = (id: string) => {
  setTask(id, null)
}

type DecodedImage = {
  source: CanvasImageSource
  width: number
  height: number
  release: () => void
}

const loadImageElement = (url: string): Promise<HTMLImageElement> =>
  new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('decode failed'))
    image.src = url
  })

/** 解码图片并自动应用 EXIF 方向；createImageBitmap 不可用时退回 <img> */
const decodeImage = async (file: File): Promise<DecodedImage> => {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        release: () => bitmap.close(),
      }
    } catch {
      // 旧内核不支持 imageOrientation 或解码失败 → 走 <img> 兜底
    }
  }
  const url = URL.createObjectURL(file)
  try {
    const image = await loadImageElement(url)
    return {
      source: image,
      width: image.naturalWidth,
      height: image.naturalHeight,
      release: () => URL.revokeObjectURL(url),
    }
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  }
}

/** 把已解码图片按比例缩放并以 JPEG 质量重新编码；JPEG 无透明通道，先铺白底 */
const encodeJpeg = (
  source: CanvasImageSource,
  width: number,
  height: number,
  scale: number,
  quality: number
): Promise<Blob> =>
  new Promise((resolve, reject) => {
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(width * scale))
    canvas.height = Math.max(1, Math.round(height * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      reject(new Error('canvas unavailable'))
      return
    }
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.imageSmoothingEnabled = true
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height)
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob)
        } else {
          reject(new Error('encode failed'))
        }
      },
      'image/jpeg',
      quality
    )
  })

export type CompressOptions = {
  /** 压缩后长边上限（像素），默认 COMPRESS_MAX_EDGE */
  maxEdge?: number
  /** 目标文件大小上限（字节），默认 COMPRESS_TARGET_MB */
  targetBytes?: number
  /** true 时无视「已达标/GIF」，始终解码并重编码 JPEG（背景图统一格式用） */
  force?: boolean
}

/**
 * 非 GIF 图片自动压缩：超过目标大小时解码 → 缩放 → 重编码 JPEG，
 * 逐档降质量、仍超标再缩小长边，直到落入目标大小；返回改名 .jpg 的新 File。
 * GIF 或已在目标以内的图片原样返回（force 时除外）。失败时抛错，由调用方提示。
 */
export const compressImageFile = async (
  file: File,
  options: CompressOptions = {}
): Promise<File> => {
  const targetBytes = options.targetBytes ?? COMPRESS_TARGET_MB * MB
  const maxEdge = options.maxEdge ?? COMPRESS_MAX_EDGE
  if (!options.force && (file.size <= targetBytes || isGifFile(file))) {
    return file
  }
  const decoded = await decodeImage(file)
  try {
    let scale = Math.min(1, maxEdge / Math.max(decoded.width, decoded.height))
    for (let round = 0; round < MAX_COMPRESS_ROUNDS; round++) {
      for (const quality of JPEG_QUALITIES) {
        const blob = await encodeJpeg(decoded.source, decoded.width, decoded.height, scale, quality)
        if (blob.size <= targetBytes) {
          const name = file.name.replace(COMPRESSED_EXTS_RE, '') + '.jpg'
          return new File([blob], name, { type: 'image/jpeg', lastModified: Date.now() })
        }
      }
      scale *= DOWNSCALE_STEP
    }
    throw new Error('compress failed')
  } finally {
    decoded.release()
  }
}

/** 各扩展名对应的 MIME：部分系统（如 Windows）给不出 file.type，按扩展名补齐 */
const MIME_BY_EXT: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
}

/** 取文件的 MIME：优先用浏览器给出的类型，缺失时按扩展名推断 */
export const resolveMimeType = (file: File) => file.type || MIME_BY_EXT[getFileExt(file.name)] || ''

export const isImage = (mimeType: string) => mimeType.startsWith('image/')

/** 文件扩展名（大写），用于卡片元信息 */
export const getExtension = (name: string) => {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toUpperCase() : ''
}

export const formatSize = (bytes: number) => {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}