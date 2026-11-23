import vm from 'node:vm'
import { describe, expect, it } from 'vitest'
import ST_CORE_SOURCE from './compat/stCore.js?raw'

type IdbRequest = {
  result?: unknown
  error?: unknown
  onsuccess: (() => void) | null
  onerror: (() => void) | null
  onupgradeneeded?: (() => void) | null
}

type IdbStore = {
  get: (key: string) => IdbRequest
  put: (value: unknown, key?: string) => IdbRequest
}

type IdbTx = {
  objectStore: (name: string) => IdbStore
  oncomplete: (() => void) | null
  onabort: (() => void) | null
  onerror: (() => void) | null
  error?: unknown
}

type IdbDb = {
  createObjectStore: (name: string) => unknown
  transaction: (name: string, mode?: string) => IdbTx
}

type IdbFactory = {
  open: (name: string, version?: number) => IdbRequest
}

const loadCore = () => {
  const sandbox: Record<string, unknown> = {
    setTimeout: (fn: () => void) => setTimeout(fn, 0),
  }
  const context = vm.createContext(sandbox)
  vm.runInContext(ST_CORE_SOURCE, context)
  return sandbox.__FLYAI_ST_CORE__ as {
    installIndexedDbShim: (
      win: unknown,
      options?: {
        load?: () => unknown
        save?: (data: unknown) => void
      }
    ) => boolean
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('installIndexedDbShim', () => {
  it('没有 indexedDB 时不安装', () => {
    const ST = loadCore()
    expect(ST.installIndexedDbShim({})).toBe(false)
  })

  it('提供 open/createObjectStore/transaction/get/put 子集', async () => {
    const ST = loadCore()
    const win: Record<string, unknown> = {
      indexedDB: {},
      setTimeout: (fn: () => void) => setTimeout(fn, 0),
    }
    expect(ST.installIndexedDbShim(win)).toBe(true)
    const factory = win.indexedDB as IdbFactory

    const opened = new Promise<IdbDb>((resolve, reject) => {
      const request = factory.open('journals', 1)
      request.onupgradeneeded = () => {
        ;(request.result as IdbDb).createObjectStore('journals')
      }
      request.onsuccess = () => resolve(request.result as IdbDb)
      request.onerror = () => reject(request.error)
    })
    const db = await opened

    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('journals', 'readwrite')
      tx.objectStore('journals').put({ ops: [1] }, 'room')
      tx.oncomplete = () => resolve()
      tx.onabort = tx.onerror = () => reject(tx.error)
    })

    const stored = await new Promise<unknown>((resolve, reject) => {
      const tx = db.transaction('journals', 'readonly')
      const request = tx.objectStore('journals').get('room')
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    expect(stored).toEqual({ ops: [1] })
  })

  it('提供 load/save 时把 IndexedDB 内容交宿主持久化', async () => {
    const ST = loadCore()
    const saved: unknown[] = []
    const win: Record<string, unknown> = {
      indexedDB: {},
      setTimeout: (fn: () => void) => setTimeout(fn, 0),
    }
    expect(
      ST.installIndexedDbShim(win, {
        load: () => ({
          journals: {
            name: 'journals',
            version: 1,
            stores: { ops: { room: { value: 1 } } },
            onversionchange: null,
          },
        }),
        save: (data) => saved.push(data),
      })
    ).toBe(true)
    const db = await new Promise<IdbDb>((resolve, reject) => {
      const request = (win.indexedDB as IdbFactory).open('journals', 1)
      request.onsuccess = () => resolve(request.result as IdbDb)
      request.onerror = () => reject(request.error)
    })
    const existing = await new Promise<unknown>((resolve) => {
      const tx = db.transaction('ops', 'readonly')
      const request = tx.objectStore('ops').get('room')
      request.onsuccess = () => resolve(request.result)
    })
    expect(existing).toEqual({ value: 1 })

    await new Promise<void>((resolve) => {
      const tx = db.transaction('ops', 'readwrite')
      tx.objectStore('ops').put({ value: 2 }, 'room')
      tx.oncomplete = () => resolve()
    })
    await tick()
    await tick()
    expect(saved.length).toBeGreaterThan(0)
    const last = saved[saved.length - 1] as {
      journals?: { stores?: { ops?: Record<string, unknown> } }
    }
    expect(last.journals?.stores?.ops?.room).toEqual({ value: 2 })
  })

  it('onupgradeneeded 先于 onsuccess，且缺失键返回 undefined', async () => {
    const ST = loadCore()
    const win: Record<string, unknown> = {
      indexedDB: {},
      setTimeout: (fn: () => void) => setTimeout(fn, 0),
    }
    ST.installIndexedDbShim(win)
    const order: string[] = []
    const db = await new Promise<IdbDb>((resolve) => {
      const request = (win.indexedDB as IdbFactory).open('media', 1)
      request.onupgradeneeded = () => {
        order.push('upgrade')
        ;(request.result as IdbDb).createObjectStore('images')
      }
      request.onsuccess = () => {
        order.push('success')
        resolve(request.result as IdbDb)
      }
    })
    expect(order).toEqual(['upgrade', 'success'])
    const tx = db.transaction('images', 'readonly')
    const request = tx.objectStore('images').get('missing')
    let value: unknown = 'unset'
    request.onsuccess = () => {
      value = request.result
    }
    await tick()
    expect(value).toBeUndefined()
  })
})
