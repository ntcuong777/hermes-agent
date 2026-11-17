/**
 * A fake host for extension tests (vitest). It runs the REAL runtime, API builder and hook table, so a test
 * that passes here fails the same way in the TUI: unknown hooks throw, denied RPC rejects, failures count.
 *
 *   const host = createFakeHost({ rpc: { 'plugin.x.ping': () => ({ ok: true }) } })
 *   const entry = await host.activate(extension, { plugin: 'x' })
 *   expect(await host.run('composer.paste', { text: 'a', bracketed: true })).toEqual({ text: 'a', bracketed: true })
 */
import { createApi } from './api.js'
import {
  type ExtensionListing,
  type ExtensionModule,
  HOOKS,
  HOST_SYMBOL,
  type HookSpec,
  type NoticeLevel,
  type SlotDef,
  type StateSnapshot
} from './contract.js'
import type { Host } from './host.js'
import { createRuntime, type Entry, type Runtime, type SlotEvent } from './runtime.js'
import { memoryStore } from './store.js'

export interface FakeHostOptions {
  activateTimeoutMs?: number
  callTimeoutMs?: number
  hooks?: Readonly<Record<string, HookSpec>>
  /** Host modules by id, as the TUI publishes them to slot copies (default none). */
  host?: Record<string, unknown>
  maxFailures?: number
  maxSlotFailures?: number
  rpc?: Record<string, (params: Record<string, unknown>) => unknown>
  sdk?: Record<string, unknown>
  /** Slot table of the test (default: the real slots.json). */
  slotDefs?: Readonly<Record<string, SlotDef>>
  slowCallMs?: number
  state?: Partial<StateSnapshot>
  /** Region hashes "this TUI build" embeds, by slot id (default none: every slot is then refused). */
  upstream?: Record<string, string>
}

export interface FakeHost {
  activate(mod: ExtensionModule | { default: ExtensionModule }, listing?: Partial<ExtensionListing>): Promise<Entry>
  command(name: string, args?: string): Promise<string[]>
  /** The slot half of the Host, for `slotComponent(id, Stock, fake.host)` and `slotFn(id, stock, fake.host)`. */
  host: Host
  logs: string[]
  notices: Array<{ level: NoticeLevel; text: string }>
  rpcCalls: Array<{ method: string; params: Record<string, unknown> }>
  slotEvents: SlotEvent[]
  run(hook: string, payload: unknown): Promise<unknown>
  runtime: Runtime
  setState(patch: Partial<StateSnapshot>): void
}

const BASE_STATE: StateSnapshot = {
  busy: false,
  connected: true,
  cwd: '/work',
  model: 'test-model',
  sid: 'sid-1',
  status: 'ready',
  storedSid: null,
  title: ''
}

export const createFakeHost = (options: FakeHostOptions = {}): FakeHost => {
  // A slot copy reads its host modules when it is imported, before `activate`: publish them like the TUI does.
  Object.defineProperty(globalThis, HOST_SYMBOL, { configurable: true, value: Object.freeze({ ...options.host }) })

  const notices: FakeHost['notices'] = []
  const logs: string[] = []
  const rpcCalls: FakeHost['rpcCalls'] = []
  const slotEvents: SlotEvent[] = []
  let state: StateSnapshot = Object.freeze({ ...BASE_STATE, ...options.state })
  const listeners = new Set<(snapshot: StateSnapshot) => void>()

  const runtime = createRuntime({
    activateTimeoutMs: options.activateTimeoutMs,
    callTimeoutMs: options.callTimeoutMs,
    hooks: options.hooks ?? HOOKS,
    log: line => void logs.push(line),
    maxFailures: options.maxFailures,
    maxSlotFailures: options.maxSlotFailures,
    notify: (text, level) => void notices.push({ level, text }),
    onSlot: event => void slotEvents.push(event),
    slotDefs: options.slotDefs,
    slowCallMs: options.slowCallMs,
    store: memoryStore(),
    upstream: options.upstream ?? {}
  })

  const deps = {
    gw: {
      request: async <T>(method: string, params: Record<string, unknown> = {}): Promise<T> => {
        rpcCalls.push({ method, params })
        const answer = options.rpc?.[method]

        if (!answer) {
          throw new Error(`fake host: no answer for ${method}`)
        }

        return answer(params) as T
      }
    },
    host: Object.freeze({ ...options.host }),
    log: (message: string) => void logs.push(message),
    notify: (text: string, level: NoticeLevel) => void notices.push({ level, text }),
    sdk: Object.freeze({ ...options.sdk }),
    state: {
      get: () => state,
      subscribe: (listener: (snapshot: StateSnapshot) => void) => {
        listeners.add(listener)

        return () => void listeners.delete(listener)
      }
    }
  }

  return {
    activate: async (loaded, listing = {}) => {
      const mod = 'default' in loaded ? loaded.default : loaded
      const plugin = listing.plugin ?? 'fake'
      const name = listing.name ?? 'ext'

      const entry = runtime.track(
        {
          entry: '/fake/ext.mjs',
          granted: true,
          id: `${plugin}/${name}`,
          name,
          plugin,
          reason: '',
          rpc: [],
          root: '/fake',
          sdk: mod.sdkVersion,
          sha256: '0'.repeat(64),
          slots: [],
          title: name,
          ...listing
        },
        'loading',
        ''
      )

      await runtime.activate(entry, mod, e => createApi(runtime, e, mod.sdkVersion, deps))

      return entry
    },
    command: async (name, args = '') => {
      const printed: string[] = []

      await runtime.runCommand(name, args, text => void printed.push(text))

      return printed
    },
    host: {
      slotEpoch: runtime.slotEpoch,
      slotFailed: runtime.slotFailed,
      slotFor: runtime.slotFor,
      slotRows: runtime.slotRows,
      subscribeSlots: runtime.subscribeSlots
    } as unknown as Host,
    logs,
    notices,
    rpcCalls,
    run: (hook, payload) => runtime.run(hook, payload),
    runtime,
    slotEvents,
    setState: patch => {
      state = Object.freeze({ ...state, ...patch })
      listeners.forEach(listener => listener(state))
    }
  }
}
