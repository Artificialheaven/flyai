import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { getApiBase } from '../../../services/flyai/runtimeConfig.js'
import { Button } from '../../base/Button.js'
import styles from './AdminPanels.module.css'
import shellStyles from './ShellPanel.module.css'

/** 输出上限：长命令（yes、大日志）不能把前端内存撑爆，只保留尾部 */
const MAX_OUTPUT = 200_000

type Status = 'connecting' | 'connected' | 'closed' | 'denied'

/** 终端输出清洗：去 ANSI 控制序列与响铃，统一换行 */
const cleanOutput = (raw: string): string =>
  raw
    // eslint-disable-next-line no-control-regex -- 终端转义序列就是控制字符
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, '')
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '')
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b[@-Z\\-_]/g, '')
    // eslint-disable-next-line no-control-regex
    .replace(/\u0007/g, '')
    .replace(/\r\n?/g, '\n')

const wsUrl = (): string => {
  const base = getApiBase()
  const target = new URL(base || window.location.href, window.location.href)
  target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:'
  target.pathname = '/api/shell'
  target.search = ''
  target.hash = ''
  return target.toString()
}

/** 管理员终端：在服务进程（POC 下即 PRoot 用户态）里开一个交互 shell */
export const ShellPanel = () => {
  const t = useTranslate()
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<Status>('connecting')
  const [output, setOutput] = useState('')
  const [line, setLine] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const wsRef = useRef<WebSocket | null>(null)
  const readyRef = useRef(false)
  const termRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const outputRef = useRef('')
  const pendingRef = useRef('')
  const flushTimerRef = useRef(0)

  const flush = useCallback(() => {
    flushTimerRef.current = 0
    const raw = pendingRef.current
    pendingRef.current = ''
    const text = cleanOutput(raw)
    if (!text) {
      return
    }
    const el = termRef.current
    // 用户翻到上面看历史时不打断，只在贴底时跟随
    const stick = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 40
    const next = outputRef.current + text
    outputRef.current =
      next.length > MAX_OUTPUT ? next.slice(next.length - MAX_OUTPUT) : next
    setOutput(outputRef.current)
    if (el && stick) {
      el.scrollTop = el.scrollHeight
    }
  }, [])

  // 输出先攒 50ms 再渲染，避免大段日志/进度条把主线程刷爆
  const append = useCallback(
    (raw: string) => {
      pendingRef.current += raw
      if (pendingRef.current.length > MAX_OUTPUT) {
        pendingRef.current = pendingRef.current.slice(-MAX_OUTPUT)
      }
      if (flushTimerRef.current === 0) {
        flushTimerRef.current = window.setTimeout(flush, 50)
      }
    },
    [flush]
  )

  const sendResize = useCallback((ws: WebSocket | null) => {
    if (!ws || ws.readyState !== WebSocket.OPEN || !readyRef.current) {
      return
    }
    const el = termRef.current
    const cols = el ? Math.max(20, Math.min(200, Math.floor(el.clientWidth / 8))) : 80
    const rows = el ? Math.max(5, Math.min(60, Math.floor(el.clientHeight / 18))) : 24
    ws.send(JSON.stringify({ type: 'resize', cols, rows }))
  }, [])

  useEffect(() => {
    const token = useAuth.getState().token
    if (!token) {
      setStatus('denied')
      return
    }
    let disposed = false
    let ws: WebSocket
    try {
      ws = new WebSocket(wsUrl())
    } catch {
      setStatus('closed')
      return
    }
    wsRef.current = ws
    readyRef.current = false
    ws.binaryType = 'arraybuffer'
    setStatus('connecting')
    const decoder = new TextDecoder('utf-8')

    ws.onopen = () => {
      // 令牌放在首条消息里，避免出现在 URL / 访问日志中
      ws.send(JSON.stringify({ type: 'auth', token }))
    }
    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data) as { type?: string }
          if (msg.type === 'ready') {
            readyRef.current = true
            setStatus('connected')
            sendResize(ws)
            window.setTimeout(() => inputRef.current?.focus(), 0)
          }
          return
        } catch {
          append(event.data)
          return
        }
      }
      append(decoder.decode(new Uint8Array(event.data as ArrayBuffer), { stream: true }))
    }
    ws.onclose = (event) => {
      readyRef.current = false
      if (wsRef.current === ws) {
        wsRef.current = null
      }
      if (!disposed) {
        setStatus(event.code === 4401 ? 'denied' : 'closed')
      }
    }
    const onResize = () => sendResize(ws)
    window.addEventListener('resize', onResize)
    return () => {
      disposed = true
      readyRef.current = false
      window.removeEventListener('resize', onResize)
      if (flushTimerRef.current) {
        window.clearTimeout(flushTimerRef.current)
        flushTimerRef.current = 0
      }
      pendingRef.current = ''
      try {
        ws.close()
      } catch {
        // 已关闭
      }
      if (wsRef.current === ws) {
        wsRef.current = null
      }
    }
  }, [attempt, append, sendResize])

  const sendInput = (data: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN || status !== 'connected') {
      return
    }
    ws.send(JSON.stringify({ type: 'input', data }))
  }

  const submit = () => {
    if (status !== 'connected') {
      return
    }
    sendInput(line + '\n')
    if (line.trim()) {
      setHistory((prev) => [...prev.filter((item) => item !== line), line].slice(-50))
    }
    setLine('')
    setHistoryIndex(-1)
  }

  const navigateHistory = (step: number) => {
    if (history.length === 0) {
      return
    }
    const current = historyIndex < 0 ? history.length : historyIndex
    const next = Math.max(0, Math.min(history.length, current + step))
    setHistoryIndex(next >= history.length ? -1 : next)
    setLine(next >= history.length ? '' : history[next])
  }

  const statusText = {
    connecting: t('shellConnecting'),
    connected: t('shellConnected'),
    closed: t('shellDisconnected'),
    denied: t('shellAuthFailed'),
  }[status]

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('shellTitle')}</div>
          <div className={styles.subtitle}>{t('shellSubtitle')}</div>
        </div>
        <div className={shellStyles.actions}>
          <span
            className={clsx(
              shellStyles.status,
              status === 'connected' && shellStyles.statusOk,
              (status === 'denied' || status === 'closed') && shellStyles.statusBad
            )}
          >
            {statusText}
          </span>
          <Button
            size="s"
            variant="outlined"
            onClick={() => {
              if (flushTimerRef.current) {
                window.clearTimeout(flushTimerRef.current)
                flushTimerRef.current = 0
              }
              pendingRef.current = ''
              outputRef.current = ''
              setOutput('')
            }}
          >
            {t('shellClear')}
          </Button>
          <Button size="s" variant="outlined" onClick={() => setAttempt((n) => n + 1)}>
            {t('shellReconnect')}
          </Button>
        </div>
      </div>

      <div ref={termRef} className={shellStyles.terminal} role="log" aria-live="polite">
        {output || <span className={shellStyles.placeholder}>{t('shellEmpty')}</span>}
      </div>

      <form
        className={shellStyles.inputRow}
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <Button
          size="s"
          variant="outlined"
          type="button"
          disabled={status !== 'connected'}
          onClick={() => sendInput('\u0003')}
        >
          Ctrl+C
        </Button>
        <input
          ref={inputRef}
          className={shellStyles.input}
          value={line}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          placeholder={t('shellInputPlaceholder')}
          disabled={status !== 'connected'}
          onChange={(event) => setLine(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowUp') {
              event.preventDefault()
              navigateHistory(-1)
            } else if (event.key === 'ArrowDown') {
              event.preventDefault()
              navigateHistory(1)
            }
          }}
        />
        <Button size="s" type="submit" disabled={status !== 'connected'}>
          {t('shellSend')}
        </Button>
      </form>

      <div className={styles.hint}>{t('shellHint')}</div>
    </div>
  )
}
