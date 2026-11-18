/**
 * TUI extension SDK: the contract (fork-owned, no React, no Ink, no store imports).
 *
 * A Python plugin ships `tui/<name>.mjs`. The host plugin `tui-host` lists the granted ones
 * (`plugin.tui-host.list`); the TUI verifies the file against the listed sha256, imports it
 * and calls `activate(api)`. Extension files have no import path to the bundle, so everything
 * they may touch arrives through `api`. Rules for extension authors: docs in spec-06.
 */

/** The SDK this TUI implements. Bump on any breaking change to `ExtensionApi` or a hook payload. */
export const SDK_VERSION = 1
/** The oldest SDK an extension may still target; older ones are refused, newer ones run through `shims`. */
export const MIN_SDK_VERSION = 1
/** Shape version of the host plugin's listing. */
export const LISTING_VERSION = 1
/** The gateway method of the host plugin (`plugin.<plugin_id>.<name>`, plugin id `tui-host`). */
export const LIST_METHOD = 'plugin.tui-host.list'
/** Where an extension reaches the host modules a slot copy imports (a copy's imports run before `activate`). */
export const HOST_SYMBOL = Symbol.for('hermes.tui.ext.host')
/** Failures of one slot (a render that throws, a function that throws or is slow) before it is switched off. */
export const SLOT_MAX_FAILURES = 3

export type NoticeLevel = 'error' | 'info' | 'warn'

/**
 * How the results of the handlers registered for one hook combine:
 * - `transform`: handlers run in order, each gets the payload so far; a returned object is merged
 *   into it, `undefined` keeps it, `null` cancels the whole operation (the caller gets `null`).
 * - `first`: handlers run in order until one returns a value that is not null/undefined.
 * - `all`: every handler runs, results are ignored (events).
 */
export type HookMode = 'all' | 'first' | 'transform'

export interface HookSpec {
  mode: HookMode
}

/**
 * The hooks this SDK version defines. Adding an entry is a minor change, removing or re-typing one is
 * a breaking change (bump SDK_VERSION). Stage 1 of spec-06b defines `composer.paste` and
 * `composer.clipboardImage`; later stages add completion, session switcher and approval hooks.
 */
export const HOOKS: Readonly<Record<string, HookSpec>> = Object.freeze({
  /** Payload `{}`. The first handler to return a non-empty string wins: the absolute path of a PNG it staged from the system clipboard. */
  'composer.clipboardImage': { mode: 'first' },
  /** Payload `{ text, bracketed }`. Return `{ text }` to replace the pasted text, `null` to swallow the paste, nothing to keep it. */
  'composer.paste': { mode: 'transform' },
  'gateway.event': { mode: 'all' }
})

/**
 * What a slot is. `component`: a React component the core renders (`slotComponent`); `fn`: a plain function the core
 * calls (`slotFn`); `hook`: a React hook the core calls (`slotHook`, resolved once per mount, fail-stop).
 * `props` is the version of the slot's argument contract: bump it when the props or arguments change shape.
 * `file` and `symbols` name the upstream region a copy is written against (see slotRegion.mjs); `host` lists the
 * host module ids a copy may import. The table is slots.json.
 */
export type SlotKind = 'component' | 'fn' | 'hook'

export interface SlotDef {
  file: string
  host: string[]
  kind: SlotKind
  props: number
  summary: string
  symbols: string[]
}

/** One slot an extension declares in plugin.yaml, with the provenance recorded in the plugin's tui_slots.lock.json. */
export interface SlotDeclaration {
  /** Hash of the upstream region the copy was written against (algo `sha256-lf-region`). */
  expectUpstream: string
  id: string
  /** What a drifted region means: `apply` (default, plugin-first: the copy is mounted and the drift reported) or `skip`. */
  onDrift?: 'apply' | 'skip'
  props: number
  reason: string
  /** What the recorded snapshot is: `upstream` (default) or `fork-core` (the fork's own core text; spec 09 option A). */
  snapshotKind?: 'fork-core' | 'upstream'
  /** The commit the snapshot is of: the upstream commit, or for `fork-core` the fork commit. */
  upstreamCommit: string
}

export type SlotStatus =
  | 'active'
  | 'disabled-after-errors'
  | 'pending'
  | 'skipped-drift'
  | 'skipped-error'
  | 'skipped-incompatible'
  | 'skipped-no-grant'

/** What an extension may read about the app. A frozen copy; there is no way to write back. */
export interface StateSnapshot {
  busy: boolean
  connected: boolean
  cwd: string
  model: string
  sid: null | string
  status: string
  storedSid: null | string
  title: string
}

export interface StateSource {
  get(): StateSnapshot
  subscribe(listener: (snapshot: StateSnapshot) => void): () => void
}

export interface CommandContext {
  /** Print one system line in the transcript. */
  print(text: string): void
}

export interface CommandDef {
  help: string
  run(args: string, ctx: CommandContext): Promise<void> | void
}

/** What `activate(api)` receives. Frozen. */
export interface ExtensionApi {
  readonly id: string
  readonly plugin: string
  /** The SDK version this extension declared. */
  readonly sdkVersion: number
  hooks: { on(name: string, handler: (payload: any) => any): () => void }
  log(message: string): void
  notify(text: string, level?: NoticeLevel): void
  onDispose(fn: () => void): void
  registerCommand(name: string, def: CommandDef): void
  rpc<T = unknown>(method: string, params?: Record<string, unknown>, opts?: { timeoutMs?: number }): Promise<T>
  /** Host modules by id (`react`, `ink`, ...): what a hand-written slot component is built from. */
  host: Readonly<Record<string, unknown>>
  /** The widget SDK of the TUI (React, h, Box, Text, defineWidgetApp, ...): overlays and panels. */
  sdk: Readonly<Record<string, unknown>>
  /** Offer a replacement for a core slot. The id must be declared under `slots:` of the extension in plugin.yaml. */
  slots: { register(id: string, impl: unknown): void }
  state: StateSource
}

/** What an extension file exports (`export default {...}` or the same names as named exports). */
export interface ExtensionModule {
  activate(api: ExtensionApi): Promise<void> | void
  sdkVersion: number
}

/** One entry of the host plugin's listing. */
export interface ExtensionListing {
  entry: string
  granted: boolean
  id: string
  name: string
  plugin: string
  reason: string
  /** Extra core RPC methods the extension may call (exact names or `prefix.*`), declared in plugin.yaml. */
  rpc: string[]
  root: string
  sdk: number
  sha256: string
  /** The slots this extension replaces, with their recorded provenance. Empty for hook-and-command extensions. */
  slots: SlotDeclaration[]
  title: string
}

export interface Listing {
  extensions: ExtensionListing[]
  version: number
}

export type ExtensionStatus = 'active' | 'disabled' | 'failed' | 'loading' | 'not-granted' | 'refused'

/** Persistence of "do not load these": `{ id: reason }`. */
export interface DisabledStore {
  read(): Record<string, string>
  write(next: Record<string, string>): void
}
