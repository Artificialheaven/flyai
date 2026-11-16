/** 断线重连退避策略，纯函数便于单测；数值与旧版 chat-stream.js 完全对齐。 */

export const MAX_RECONNECT_ATTEMPTS = 8

/** 第 n 次重连（1 起算）的等待毫秒：500 * 1.5^(n-1)，封顶 8 秒 */
export const backoffDelay = (attempt: number): number =>
  Math.min(8000, Math.round(500 * Math.pow(1.5, attempt - 1)))

export const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))
