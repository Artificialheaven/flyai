/**
 * POC 应用内相册：通过 FlyAIPoc 原生桥浏览系统相册（MediaStore）。
 *
 * 列表接口返回缩略图 data URI；选中后按需读取原图 base64，网页侧转成 File
 * 走正常的附件压缩/上传流程。只在 POC APK 里可用。
 */

type GalleryBridge = {
  galleryPermission?: (token: string) => string
  requestGalleryPermission?: (token: string) => string
  listGallery?: (token: string, offset: string, limit: string) => string
  readGalleryImage?: (token: string, id: string) => string
  openAppSettings?: (token: string) => string
}

const bridgeInfo = (): { token: string; bridge: GalleryBridge } | null => {
  if (typeof window === 'undefined') {
    return null
  }
  const win = window as unknown as {
    __flyaiPoc?: { token?: string }
    FlyAIPoc?: GalleryBridge
  }
  const token = win.__flyaiPoc?.token
  if (!token || !win.FlyAIPoc) {
    return null
  }
  return { token, bridge: win.FlyAIPoc }
}

const parse = <T>(raw: string | null | undefined): T | null => {
  try {
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

export type GalleryImage = {
  id: string
  name: string
  mime: string
  date: number
  /** 240px JPEG data URI；生成失败时缺失 */
  thumb?: string
}

export const isGallerySupported = (): boolean => {
  const info = bridgeInfo()
  return Boolean(info?.bridge.listGallery && info?.bridge.readGalleryImage)
}

export const getGalleryPermission = (): boolean => {
  const info = bridgeInfo()
  if (!info?.bridge.galleryPermission) {
    return false
  }
  try {
    const data = parse<{ granted?: boolean }>(
      info.bridge.galleryPermission(info.token)
    )
    return data?.granted === true
  } catch {
    return false
  }
}

export const requestGalleryPermission = (): void => {
  const info = bridgeInfo()
  if (!info?.bridge.requestGalleryPermission) {
    return
  }
  try {
    info.bridge.requestGalleryPermission(info.token)
  } catch {
    // 忽略：由调用方轮询权限状态
  }
}

export const openAppSettingsPage = (): void => {
  const info = bridgeInfo()
  if (!info?.bridge.openAppSettings) {
    return
  }
  try {
    info.bridge.openAppSettings(info.token)
  } catch {
    // 打不开系统设置时保持原状
  }
}

export const listGalleryImages = (
  offset: number,
  limit: number
): { images: GalleryImage[]; hasMore: boolean } | null => {
  const info = bridgeInfo()
  if (!info?.bridge.listGallery) {
    return null
  }
  try {
    const data = parse<{
      ok?: boolean
      images?: GalleryImage[]
      hasMore?: boolean
    }>(info.bridge.listGallery(info.token, String(offset), String(limit)))
    if (!data?.ok) {
      return null
    }
    return {
      images: Array.isArray(data.images) ? data.images : [],
      hasMore: data.hasMore === true,
    }
  } catch {
    return null
  }
}

/** 读取相册原图并转成 File（文件名/类型沿用相册元数据） */
export const readGalleryFile = (image: GalleryImage): File | null => {
  const info = bridgeInfo()
  if (!info?.bridge.readGalleryImage) {
    return null
  }
  try {
    const data = parse<{
      ok?: boolean
      name?: string
      mime?: string
      data?: string
    }>(info.bridge.readGalleryImage(info.token, image.id))
    if (!data?.ok || !data.data) {
      return null
    }
    const binary = atob(data.data)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index)
    }
    return new File(
      [bytes],
      data.name || image.name || `image_${image.id}`,
      { type: data.mime || image.mime || 'image/jpeg' }
    )
  } catch {
    return null
  }
}
