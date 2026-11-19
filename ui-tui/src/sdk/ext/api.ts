/** Builds the `api` object one extension receives. Every capability is a closure over that extension's entry. */
import { type CommandDef, type ExtensionApi, type NoticeLevel, type StateSource } from './contract.js'
import type { Entry, Runtime } from './runtime.js'

/** Never reachable from an extension, even when declared: they run commands or rewrite config. */
export const DENIED_RPC: ReadonlySet<string> = new Set(['config.set', 'shell.exec'])

const COMMAND_NAME = /^[a-z][a-z0-9-]{0,31}$/

export interface GatewayLike {
  request<T>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T>
}

export interface ApiDeps {
  gw: GatewayLike
  /** Host modules by id for hand-written slot components (default none). */
  host?: Readonly<Record<string, unknown>>
  log(message: string): void
  notify(text: string, level: NoticeLevel): void
  sdk: Readonly<Record<string, unknown>>
  state: StateSource
}

/** `plugin.<own plugin>.*` always; anything else only when plugin.yaml declared it (exact or `prefix.*`); never DENIED_RPC. */
export const rpcAllowed = (plugin: string, declared: readonly string[], method: string): boolean =>
  !DENIED_RPC.has(method) &&
  (method.startsWith(`plugin.${plugin}.`) ||
    declared.some(rule => rule === method || (rule.endsWith('.*') && method.startsWith(rule.slice(0, -1)))))

export const createApi = (runtime: Runtime, entry: Entry, sdkVersion: number, deps: ApiDeps): ExtensionApi => {
  const { id, listing } = entry

  const on: ExtensionApi['hooks']['on'] = (name, handler) => {
    if (!runtime.hookSpecs[name]) {
      throw new Error(
        `unknown hook '${name}' (this SDK defines: ${Object.keys(runtime.hookSpecs).join(', ') || 'none'})`
      )
    }

    if (typeof handler !== 'function') {
      throw new TypeError(`hook '${name}' needs a function`)
    }

    entry.hooks.set(name, [...(entry.hooks.get(name) ?? []), handler])

    return () =>
      void entry.hooks.set(
        name,
        (entry.hooks.get(name) ?? []).filter(fn => fn !== handler)
      )
  }

  const registerCommand = (name: string, def: CommandDef): void => {
    if (!COMMAND_NAME.test(name)) {
      throw new Error(`invalid command name '${name}' (lowercase letters, digits, '-')`)
    }

    const owner = runtime.commandOwner(name)

    if (owner && owner.id !== id) {
      throw new Error(`command '${name}' is already registered by ${owner.id}`)
    }

    if (!def || typeof def.run !== 'function') {
      throw new TypeError(`command '${name}' needs run(args, ctx)`)
    }

    entry.commands.set(name, def)
  }

  return Object.freeze({
    hooks: Object.freeze({ on }),
    host: deps.host ?? Object.freeze({}),
    id,
    log: (message: string) => deps.log(`[ext ${id}] ${message}`),
    notify: (text: string, level: NoticeLevel = 'info') => deps.notify(`[${id}] ${text}`, level),
    onDispose: (fn: () => void) => void entry.disposers.push(fn),
    plugin: listing.plugin,
    registerCommand,
    rpc: <T>(method: string, params: Record<string, unknown> = {}, opts?: { timeoutMs?: number }) =>
      rpcAllowed(listing.plugin, listing.rpc, method)
        ? deps.gw.request<T>(method, params, opts?.timeoutMs ?? 15_000)
        : Promise.reject(
            new Error(`rpc ${method} is not allowed for ${id}: declare it under tui_extensions[].rpc in plugin.yaml`)
          ),
    sdk: deps.sdk,
    sdkVersion,
    slots: Object.freeze({ register: (slotId: string, impl: unknown) => runtime.registerSlot(entry, slotId, impl) }),
    state: deps.state
  })
}
