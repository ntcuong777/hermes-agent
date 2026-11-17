/**
 * The extension host: one per TUI process. Core code talks to extensions ONLY through this object
 * (`runHook`, `has`, `runCommand`, `slotFor`, `slotFailed`), so with no extension loaded (or no `attach` yet) every
 * call is a cheap no-op.
 */
import { type ApiDeps, createApi, type GatewayLike } from './api.js'
import { type DisabledStore, HOOKS, HOST_SYMBOL, type NoticeLevel, type SlotDef, type StateSource } from './contract.js'
import { defaultCachePath, readListingCache, writeListingCache } from './listingCache.js'
import { fetchListing, type LoadReport, loadOne, parseOne, syncListing } from './loader.js'
import { createRuntime, type Runtime, type SlotRow, type Watchdog } from './runtime.js'
import { fileStatusSink, type StatusSink } from './slotStatus.js'
import { fileStore } from './store.js'

export interface HostDeps {
  /** Where the last listing is kept for the next start's early load (default `<home>/tui-extensions-cache.json`). */
  cachePath?: string
  /** True for the attach before the first frame: notices are held until the UI attaches (second `attach`). */
  deferNotices?: boolean
  gw: GatewayLike
  /** Host modules by id (`react`, `ink`, ...) for slot copies and hand-written slot components. */
  host?: Readonly<Record<string, unknown>>
  importer?: (url: string) => Promise<unknown>
  log(message: string): void
  notify(text: string, level: NoticeLevel): void
  sdk: Readonly<Record<string, unknown>>
  slotDefs?: Readonly<Record<string, SlotDef>>
  state: StateSource
  status?: StatusSink
  store?: DisabledStore
  /** Region hashes of this build's upstream files (default: the table the build embedded). */
  upstream?: Readonly<Record<string, string>>
  watchdog?: Watchdog
}

export interface Host {
  attach(deps: HostDeps): void
  detach(): void
  disable(id: string): string
  enable(id: string): Promise<LoadReport[]>
  has(name: string): boolean
  /** Why the host is idle, or '' when it is serving. */
  idleReason(): string
  /** Mount the slot extensions of the cached listing (before the first frame). Never throws. */
  preload(): Promise<LoadReport[]>
  refresh(): Promise<LoadReport[]>
  reload(id?: string): Promise<LoadReport[]>
  runCommand(name: string, args: string, print: (text: string) => void): Promise<boolean>
  runHook(name: string, payload: unknown): Promise<unknown>
  runtime(): null | Runtime
  slotEpoch(): number
  slotFailed(id: string, message: string, opts?: { immediate?: boolean }): void
  slotFor(id: string): undefined | { extId: string; impl: unknown }
  slotRows(): SlotRow[]
  subscribeSlots(listener: () => void): () => void
}

export const extensionsDisabledByEnv = (env: NodeJS.ProcessEnv = process.env): boolean =>
  env.HERMES_TUI_EXTENSIONS === '0'

interface Current {
  deps: HostDeps
  held: Array<[string, NoticeLevel]>
  runtime: Runtime
  unsub: () => void
}

export const createHost = (): Host => {
  let current: Current | null = null
  let idle = 'not attached yet'
  let queue: Promise<unknown> = Promise.resolve()
  let epoch = 0
  let snapshotQueued = false
  const listeners = new Set<() => void>()

  const bump = (): void => {
    epoch += 1
    listeners.forEach(listener => listener())
  }

  // Refreshes never overlap: two listings racing would activate one extension twice.
  const serial = <T>(fn: () => Promise<T>): Promise<T> => {
    const next = queue.then(fn, fn)

    queue = next.catch(() => {})

    return next
  }

  const loaderDeps = () => {
    if (!current) {
      return null
    }

    const { deps, runtime } = current

    const apiDeps: ApiDeps = {
      gw: deps.gw,
      host: deps.host,
      log: deps.log,
      notify: deps.notify,
      sdk: deps.sdk,
      state: deps.state
    }

    return {
      buildApi: (entry: Parameters<typeof createApi>[1], version: number) =>
        createApi(runtime, entry, version, apiDeps),
      importer: deps.importer,
      runtime
    }
  }

  const sync = async (): Promise<LoadReport[]> => {
    const loader = loaderDeps()

    if (!current || !loader) {
      return []
    }

    const { deps } = current
    const listed = await fetchListing(deps.gw)

    if (!listed.ok) {
      idle = listed.detail

      return []
    }

    idle = ''
    writeListingCache(deps.cachePath ?? defaultCachePath(), listed.listing)

    return syncListing(loader, listed.listing)
  }

  return {
    attach: deps => {
      if (extensionsDisabledByEnv()) {
        idle = 'HERMES_TUI_EXTENSIONS=0'

        return
      }

      Object.defineProperty(globalThis, HOST_SYMBOL, { configurable: true, value: deps.host ?? {} })

      if (current) {
        // The UI attaches after the early load: keep runtime and entries, swap the live dependencies, flush held notices.
        current.deps = deps
        current.held.splice(0).forEach(([text, level]) => deps.notify(text, level))

        return
      }

      const sink = deps.status ?? fileStatusSink()
      const held: Array<[string, NoticeLevel]> = []

      const route = (text: string, level: NoticeLevel): void =>
        current?.deps.deferNotices ? void held.push([text, level]) : current?.deps.notify(text, level)

      const runtime = createRuntime({
        announce: key => sink.announce(key),
        log: line => current?.deps.log(line),
        notify: route,
        onSlot: event => sink.event(event),
        slotDefs: deps.slotDefs,
        store: deps.store ?? fileStore(),
        upstream: deps.upstream,
        watchdog: deps.watchdog
      })

      const unsub = runtime.subscribeSlots(() => {
        bump()

        if (!snapshotQueued) {
          snapshotQueued = true
          queueMicrotask(() => {
            snapshotQueued = false
            sink.snapshot(runtime.slotRows())
          })
        }
      })

      current = { deps, held, runtime, unsub }
      idle = 'no listing yet'
      bump()
    },
    detach: () => {
      current?.runtime.entries().forEach(entry => current?.runtime.untrack(entry.id))
      current?.unsub()
      current = null
      idle = 'not attached yet'
      bump()
    },
    disable: id => {
      if (current && current.runtime.slotRows().some(row => row.id === id)) {
        return current.runtime.disableSlot(id, 'disabled with /extensions disable')
      }

      const entry = current?.runtime.get(id)

      if (!current || !entry) {
        return `no extension or slot ${id}`
      }

      current.runtime.disable(entry, 'disabled with /extensions disable', { persist: true, silent: true })

      return `${id} disabled (persisted; /extensions enable ${id} to undo)`
    },
    enable: id =>
      serial(async () => {
        const owner = current?.runtime.slotRows().find(row => row.id === id)

        current?.runtime.enable(id)
        current?.runtime.untrack(owner ? owner.extId : id)

        return sync()
      }),
    has: name => current?.runtime.hasHook(name) ?? false,
    idleReason: () => idle,
    preload: () =>
      serial(async () => {
        const loader = loaderDeps()

        if (!current || !loader) {
          return []
        }

        const cached = readListingCache(current.deps.cachePath ?? defaultCachePath(), parseOne)
        const rows = (cached?.extensions ?? []).filter(item => item.granted && item.slots.length > 0)
        const reports: LoadReport[] = []

        for (const item of rows) {
          reports.push(await loadOne(loader, item))
        }

        return reports
      }).catch(() => []),
    refresh: () => serial(sync),
    reload: id =>
      serial(async () => {
        if (id) {
          current?.runtime.untrack(id)
        } else {
          current?.runtime.entries().forEach(entry => current?.runtime.untrack(entry.id))
        }

        return sync()
      }),
    runCommand: async (name, args, print) => (current ? current.runtime.runCommand(name, args, print) : false),
    runHook: async (name, payload) => {
      if (!current) {
        return HOOKS[name]?.mode === 'first' ? null : payload
      }

      return current.runtime.run(name, payload)
    },
    runtime: () => current?.runtime ?? null,
    slotEpoch: () => epoch,
    slotFailed: (id, message, opts) => current?.runtime.slotFailed(id, message, opts),
    slotFor: id => current?.runtime.slotFor(id),
    slotRows: () => current?.runtime.slotRows() ?? [],
    subscribeSlots: listener => {
      listeners.add(listener)

      return () => void listeners.delete(listener)
    }
  }
}

/** The process-wide host. Tests build their own with `createHost()`. */
export const extensionHost: Host = createHost()
