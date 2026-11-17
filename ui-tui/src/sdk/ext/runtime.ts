/**
 * TUI extension SDK: the runtime. Owns every extension's state and is the ONLY place that calls
 * extension code, always through `guard` (try/catch, timeout, failure count, auto-disable).
 *
 * Imperative shell on purpose: entries are mutable records because hooks and commands are
 * registered by extension code over time. Nothing outside this file mutates them.
 */
import {
  type CommandDef,
  type DisabledStore,
  type ExtensionApi,
  type ExtensionListing,
  type ExtensionModule,
  type ExtensionStatus,
  HOOKS,
  type HookSpec,
  MIN_SDK_VERSION,
  type NoticeLevel,
  SDK_VERSION,
  SLOT_MAX_FAILURES,
  type SlotDeclaration,
  type SlotDef,
  type SlotStatus
} from './contract.js'
import { checkSlot, mountLine, SLOT_DEFS, UPSTREAM_HASHES } from './slots.js'

export type Handler = (payload: any) => any

/** One slot an extension registered (or declared and failed to register). */
export interface SlotReg {
  decl: SlotDeclaration
  detail: string
  failures: number
  impl: unknown
  status: SlotStatus
}

/** What `/extensions slots`, the status file and the events list. */
export interface SlotRow {
  detail: string
  extId: string
  failures: number
  id: string
  kind: string
  props: number
  reason: string
  status: SlotStatus
  upstreamCommit: string
}

/** The names the Python registry uses (docs/agents/core-patches.md section 4), so one reader serves both. */
export type SlotEventKind = 'applied' | 'auto-disabled' | 'drift-first-seen' | 'drift-recovered' | 'error' | 'skipped'

export interface SlotEvent {
  detail: string
  extId: string
  kind: SlotEventKind
  slot: string
  status: SlotStatus
  upstreamCommit: string
}

export interface Entry {
  commands: Map<string, CommandDef>
  detail: string
  disposers: Array<() => void>
  errors: string[]
  failures: number
  hooks: Map<string, Handler[]>
  readonly id: string
  listing: ExtensionListing
  slots: Map<string, SlotReg>
  status: ExtensionStatus
}

/** Marks "extension code is running on the main thread" for the hang watchdog (see watchdog.ts). */
export interface Watchdog {
  enter(id: string): void
  leave(): void
}

export interface RuntimeOptions {
  /** Time one extension may run `activate` (default 3000). */
  activateTimeoutMs?: number
  /** True when a transcript notice with this key should be shown now (the host dedupes per 24 h). Default: always. */
  announce?(key: string): boolean
  /** Time one hook or command handler may take to settle (default 2000). */
  callTimeoutMs?: number
  hooks?: Readonly<Record<string, HookSpec>>
  /** One log line per slot decision (mounted or skipped): slot, extension, upstream commit. */
  log?(line: string): void
  /** Failures (throw, rejection, timeout, slow synchronous part) before an extension is disabled (default 3). */
  maxFailures?: number
  maxSlotFailures?: number
  minSdk?: number
  notify(text: string, level: NoticeLevel): void
  now?(): number
  /** Called on every slot decision and failure; the host writes the status file and the events from it. */
  onSlot?(event: SlotEvent): void
  sdk?: number
  /** `shims[N]` adapts the current API to the shape of SDK N, for extensions that declare an older N. */
  shims?: Readonly<Record<number, (api: ExtensionApi) => ExtensionApi>>
  /** A synchronous part longer than this counts as a failure (default 250). */
  slotDefs?: Readonly<Record<string, SlotDef>>
  slowCallMs?: number
  store: DisabledStore
  /** Region hashes of the upstream files this build was made from (default: the table the build embedded). */
  upstream?: Readonly<Record<string, string>>
  watchdog?: Watchdog
}

export type Guarded<T> = { ok: false } | { ok: true; value: T }

export type SdkCheck = { ok: false; detail: string } | { ok: true; shim: ((api: ExtensionApi) => ExtensionApi) | null }

export interface Runtime {
  activate(entry: Entry, mod: ExtensionModule, buildApi: (entry: Entry) => ExtensionApi): Promise<void>
  checkSdk(declared: unknown): SdkCheck
  commandOwner(name: string): Entry | undefined
  /** After `activate`: a declared slot the extension never registered is a visible error, not a silent gap. */
  finishSlots(entry: Entry): void
  disable(entry: Entry, reason: string, opts?: { persist?: boolean; silent?: boolean }): void
  disableSlot(id: string, reason: string): string
  enable(id: string): void
  entries(): Entry[]
  get(id: string): Entry | undefined
  guard<T>(entry: Entry, label: string, fn: () => Promise<T> | T, timeoutMs?: number): Promise<Guarded<T>>
  hasHook(name: string): boolean
  hookSpecs: Readonly<Record<string, HookSpec>>
  isDisabled(id: string): null | string
  /** Persist "was loading" for `id` until the returned function runs: a hang or crash during import leaves the mark, and the next start does not load it. */
  markLoading(id: string): () => void
  notify(text: string, level: NoticeLevel): void
  /** Mount (or refuse, with the verdict as status) an extension's replacement for a core slot. Throws on a misuse. */
  registerSlot(entry: Entry, id: string, impl: unknown): void
  run(name: string, payload: unknown): Promise<unknown>
  runCommand(name: string, args: string, print: (text: string) => void): Promise<boolean>
  setStatus(entry: Entry, status: ExtensionStatus, detail: string): void
  slotEpoch(): number
  /** A mounted slot failed (render throw, function throw, slow call). `immediate` switches it off at once (hook slots). */
  slotFailed(id: string, message: string, opts?: { immediate?: boolean }): void
  /** The extension implementation of `id`, only while its extension and the slot are active. */
  slotFor(id: string): undefined | { extId: string; impl: unknown }
  slotRows(): SlotRow[]
  subscribeSlots(listener: () => void): () => void
  track(listing: ExtensionListing, status: ExtensionStatus, detail: string): Entry
  untrack(id: string): void
}

const MAX_ERRORS = 10
const noop = () => {}

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error))

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Dispose everything an entry registered. Disposer errors are swallowed: a dead extension cannot veto cleanup. */
const dispose = (entry: Entry): void => {
  for (const fn of [...entry.disposers].reverse()) {
    try {
      fn()
    } catch {
      // ignored on purpose
    }
  }

  entry.disposers = []
  entry.hooks.clear()
  entry.commands.clear()
  entry.slots.clear()
}

export const createRuntime = (options: RuntimeOptions): Runtime => {
  const sdk = options.sdk ?? SDK_VERSION
  const minSdk = options.minSdk ?? MIN_SDK_VERSION
  const hookSpecs = options.hooks ?? HOOKS
  const maxFailures = options.maxFailures ?? 3
  const callTimeoutMs = options.callTimeoutMs ?? 2000
  const activateTimeoutMs = options.activateTimeoutMs ?? 3000
  const slowCallMs = options.slowCallMs ?? 250
  const now = options.now ?? Date.now
  const slotDefs = options.slotDefs ?? SLOT_DEFS
  const upstream = options.upstream ?? UPSTREAM_HASHES
  const maxSlotFailures = options.maxSlotFailures ?? SLOT_MAX_FAILURES
  const table = new Map<string, Entry>()
  const slotListeners = new Set<() => void>()
  let epoch = 0

  const changed = (): void => {
    epoch += 1
    slotListeners.forEach(listener => listener())
  }

  const entries = (): Entry[] => [...table.values()].sort((a, b) => a.id.localeCompare(b.id))

  const active = (): Entry[] => entries().filter(entry => entry.status === 'active')

  const disable: Runtime['disable'] = (entry, reason, opts = {}) => {
    dispose(entry)
    entry.status = 'disabled'
    entry.detail = reason
    changed()

    if (opts.persist) {
      options.store.write({ ...options.store.read(), [entry.id]: reason })
    }

    if (!opts.silent) {
      options.notify(
        `extension ${entry.id} disabled: ${reason} (re-enable with /extensions enable ${entry.id})`,
        'warn'
      )
    }
  }

  const fail = (entry: Entry, label: string, message: string): void => {
    entry.failures += 1
    entry.errors = [...entry.errors, `${new Date(now()).toISOString()} ${label}: ${message}`].slice(-MAX_ERRORS)

    if (entry.failures >= maxFailures) {
      disable(entry, `${entry.failures} failures, last: ${label}: ${message}`, { persist: true })
    } else if (entry.failures === 1) {
      options.notify(`extension ${entry.id}: ${label} failed: ${message} (/extensions errors ${entry.id})`, 'warn')
    }
  }

  const guard: Runtime['guard'] = async (entry, label, fn, timeoutMs = callTimeoutMs) => {
    let timer: ReturnType<typeof setTimeout> | undefined

    try {
      const started = now()
      let raw: Promise<unknown> | unknown

      options.watchdog?.enter(entry.id)

      try {
        raw = fn()
      } finally {
        options.watchdog?.leave()
      }

      const syncMs = now() - started
      const pending = Promise.resolve(raw)

      pending.catch(noop) // a rejection that arrives after the timeout won must not become unhandled

      const value = (await Promise.race([
        pending,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(`timed out after ${timeoutMs}ms`)), timeoutMs)
          timer.unref?.()
        })
      ])) as Awaited<ReturnType<typeof fn>>

      if (syncMs > slowCallMs) {
        fail(entry, label, `blocked the UI for ${syncMs}ms (limit ${slowCallMs}ms)`)
      }

      return { ok: true, value }
    } catch (error) {
      fail(entry, label, messageOf(error))

      return { ok: false }
    } finally {
      clearTimeout(timer)
    }
  }

  const checkSdk: Runtime['checkSdk'] = declared => {
    if (typeof declared !== 'number' || !Number.isInteger(declared) || declared < 1) {
      return { detail: `declares an invalid sdkVersion (${String(declared)})`, ok: false }
    }

    if (declared > sdk) {
      return {
        detail: `needs SDK ${declared}, this TUI has SDK ${sdk}: update the TUI (nix flake update hermes, rebuild)`,
        ok: false
      }
    }

    if (declared < minSdk) {
      return {
        detail: `targets SDK ${declared}, this TUI supports SDK ${minSdk}..${sdk}: update the extension`,
        ok: false
      }
    }

    if (declared === sdk) {
      return { ok: true, shim: null }
    }

    const shim = options.shims?.[declared]

    return shim
      ? { ok: true, shim }
      : {
          detail: `targets SDK ${declared} and this TUI (SDK ${sdk}) has no compat shim for it: update the extension`,
          ok: false
        }
  }

  const emit = (kind: SlotEventKind, entry: Entry, reg: SlotReg): void =>
    options.onSlot?.({
      detail: reg.detail,
      extId: entry.id,
      kind,
      slot: reg.decl.id,
      status: reg.status,
      upstreamCommit: reg.decl.upstreamCommit
    })

  const slotOwner = (id: string): Entry | undefined => entries().find(entry => entry.slots.has(id))

  const registerSlot: Runtime['registerSlot'] = (entry, id, impl) => {
    const decl = (entry.listing.slots ?? []).find(item => item.id === id)

    if (!decl) {
      throw new Error(`slot '${id}' is not declared under slots: of ${entry.id} in plugin.yaml`)
    }

    const def = slotDefs[id]

    if (!def) {
      throw new Error(`unknown slot '${id}' (this TUI defines: ${Object.keys(slotDefs).join(', ') || 'none'})`)
    }

    if (typeof impl !== 'function') {
      throw new TypeError(`slot '${id}' (${def.kind}) needs a function`)
    }

    const owner = slotOwner(id)

    if (owner && owner.id !== entry.id) {
      throw new Error(`slot '${id}' is already provided by ${owner.id}`)
    }

    const off = options.store.read()[`slot:${id}`]
    const verdict = off
      ? { detail: off, status: 'disabled-after-errors' as const }
      : checkSlot(decl, slotDefs, upstream)
    const reg: SlotReg = { decl, detail: verdict.detail, failures: 0, impl, status: verdict.status }

    entry.slots.set(id, reg)

    if (reg.status === 'active') {
      options.log?.(mountLine(decl, entry.id))
      emit('applied', entry, reg)
    } else {
      options.log?.(`tui slot ${id} ${reg.status}: ${reg.detail}`)

      if (options.announce?.(`${id}:${reg.status}:${reg.detail}`) ?? true) {
        options.notify(`slot ${id} ${reg.status}: ${reg.detail} (upstream's own behaviour runs)`, 'warn')
      }

      emit(reg.status === 'skipped-drift' ? 'drift-first-seen' : 'skipped', entry, reg)
    }

    changed()
  }

  const finishSlots: Runtime['finishSlots'] = entry => {
    for (const decl of entry.listing.slots ?? []) {
      if (!entry.slots.has(decl.id)) {
        const reg: SlotReg = {
          decl,
          detail: 'declared in plugin.yaml but the extension did not register it',
          failures: 0,
          impl: undefined,
          status: 'skipped-error'
        }

        entry.slots.set(decl.id, reg)
        options.log?.(`tui slot ${decl.id} ${reg.status}: ${reg.detail}`)
        emit('error', entry, reg)
      }
    }
  }

  const slotFor: Runtime['slotFor'] = id => {
    for (const entry of active()) {
      const reg = entry.slots.get(id)

      if (reg && reg.status === 'active') {
        return { extId: entry.id, impl: reg.impl }
      }
    }

    return undefined
  }

  const slotFailed: Runtime['slotFailed'] = (id, message, opts = {}) => {
    const entry = slotOwner(id)
    const reg = entry?.slots.get(id)

    if (!entry || !reg || reg.status !== 'active') {
      return
    }

    reg.failures += 1
    entry.errors = [...entry.errors, `${new Date(now()).toISOString()} slot ${id}: ${message}`].slice(-MAX_ERRORS)

    if (opts.immediate || reg.failures >= maxSlotFailures) {
      reg.status = 'disabled-after-errors'
      reg.detail = `${reg.failures} failure(s), last: ${message}`
      options.store.write({ ...options.store.read(), [`slot:${id}`]: reg.detail })
      options.notify(
        `slot ${id} switched off: ${reg.detail} (upstream's own behaviour runs; /extensions enable ${id} to retry)`,
        'warn'
      )
      emit('auto-disabled', entry, reg)
      changed()
    } else if (reg.failures === 1) {
      options.notify(`slot ${id} failed: ${message} (/extensions errors ${entry.id})`, 'warn')
      emit('error', entry, reg)
    }
  }

  const disableSlot: Runtime['disableSlot'] = (id, reason) => {
    const entry = slotOwner(id)
    const reg = entry?.slots.get(id)

    options.store.write({ ...options.store.read(), [`slot:${id}`]: reason })

    if (entry && reg) {
      reg.status = 'disabled-after-errors'
      reg.detail = reason
      changed()
    }

    return `slot ${id} disabled (persisted; /extensions enable ${id} to undo)`
  }

  const fallbackStatus = (entry: Entry): SlotStatus =>
    entry.status === 'not-granted'
      ? 'skipped-no-grant'
      : entry.status === 'loading'
        ? 'pending'
        : entry.status === 'disabled'
          ? 'disabled-after-errors'
          : 'skipped-error'

  const slotRows: Runtime['slotRows'] = () =>
    entries().flatMap(entry =>
      (entry.listing.slots ?? []).map(decl => {
        const reg = entry.slots.get(decl.id)

        return {
          detail: reg ? reg.detail : entry.detail,
          extId: entry.id,
          failures: reg?.failures ?? 0,
          id: decl.id,
          kind: slotDefs[decl.id]?.kind ?? '?',
          props: decl.props,
          reason: decl.reason,
          status: reg ? reg.status : fallbackStatus(entry),
          upstreamCommit: decl.upstreamCommit
        }
      })
    )

  const activate: Runtime['activate'] = async (entry, mod, buildApi) => {
    const result = await guard(entry, 'activate', () => mod.activate(buildApi(entry)), activateTimeoutMs)

    if (result.ok && entry.status !== 'disabled') {
      entry.status = 'active'
      entry.detail = ''
      finishSlots(entry)
      changed()

      return
    }

    if (entry.status !== 'disabled') {
      dispose(entry)
      entry.status = 'failed'
      entry.detail = entry.errors.at(-1) ?? 'activate failed'
      changed()
    }
  }

  const run: Runtime['run'] = async (name, payload) => {
    const spec = hookSpecs[name]

    if (!spec) {
      return payload
    }

    const handlers = (): Array<{ entry: Entry; fn: Handler }> =>
      active().flatMap(entry => (entry.hooks.get(name) ?? []).map(fn => ({ entry, fn })))

    if (spec.mode === 'all') {
      await Promise.all(handlers().map(({ entry, fn }) => guard(entry, `hook ${name}`, () => fn(payload))))

      return undefined
    }

    let current = payload

    for (const { entry, fn } of handlers()) {
      if (entry.status !== 'active') {
        continue
      }

      const result = await guard(entry, `hook ${name}`, () => fn(current))

      if (!result.ok) {
        continue
      }

      if (spec.mode === 'first') {
        if (result.value !== null && result.value !== undefined) {
          return result.value
        }

        continue
      }

      if (result.value === null) {
        return null
      }

      if (result.value !== undefined) {
        current = isPlainObject(current) && isPlainObject(result.value) ? { ...current, ...result.value } : result.value
      }
    }

    return spec.mode === 'first' ? null : current
  }

  const commandOwner: Runtime['commandOwner'] = name => active().find(entry => entry.commands.has(name))

  const runCommand: Runtime['runCommand'] = async (name, args, print) => {
    const owner = commandOwner(name)
    const def = owner?.commands.get(name)

    if (!owner || !def) {
      return false
    }

    await guard(owner, `command ${name}`, () => def.run(args, { print }), 30_000)

    return true
  }

  return {
    activate,
    checkSdk,
    commandOwner,
    disable,
    disableSlot,
    enable: id => {
      const { [id]: _removed, [`slot:${id}`]: _slot, ...rest } = options.store.read()

      options.store.write(rest)
    },
    finishSlots,
    entries,
    get: id => table.get(id),
    guard,
    hasHook: name => active().some(entry => (entry.hooks.get(name)?.length ?? 0) > 0),
    hookSpecs,
    isDisabled: id => options.store.read()[id] ?? null,
    markLoading: id => {
      const reason = `was loading when the TUI stopped (hang or crash): retry with /extensions enable ${id}`

      options.store.write({ ...options.store.read(), [id]: reason })

      return () => {
        const { [id]: current, ...rest } = options.store.read()

        if (current === reason) {
          options.store.write(rest)
        }
      }
    },
    notify: options.notify,
    registerSlot,
    run,
    runCommand,
    setStatus: (entry, status, detail) => {
      entry.status = status
      entry.detail = detail
      changed()
    },
    slotEpoch: () => epoch,
    slotFailed,
    slotFor,
    slotRows,
    subscribeSlots: listener => {
      slotListeners.add(listener)

      return () => void slotListeners.delete(listener)
    },
    track: (listing, status, detail) => {
      const previous = table.get(listing.id)

      if (previous) {
        dispose(previous)
      }

      const entry: Entry = {
        commands: new Map(),
        detail,
        disposers: [],
        errors: [],
        failures: 0,
        hooks: new Map(),
        id: listing.id,
        listing,
        slots: new Map(),
        status
      }

      table.set(listing.id, entry)
      changed()

      return entry
    },
    untrack: id => {
      const entry = table.get(id)

      if (entry) {
        dispose(entry)
        table.delete(id)
        changed()
      }
    }
  }
}
