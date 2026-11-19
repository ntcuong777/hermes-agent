/** Fetches the host plugin's listing and turns each granted entry into an active extension (or a visible refusal). */
import { createHash } from 'node:crypto'
import { readFile, realpath } from 'node:fs/promises'
import { extname, sep } from 'node:path'
import { pathToFileURL } from 'node:url'

import type { GatewayLike } from './api.js'
import {
  type ExtensionApi,
  type ExtensionListing,
  type ExtensionModule,
  LIST_METHOD,
  LISTING_VERSION,
  type Listing,
  type SlotDeclaration
} from './contract.js'
import type { Entry, Runtime } from './runtime.js'

export type ListResult = { detail: string; ok: false } | { listing: Listing; ok: true }

export interface LoadReport {
  detail: string
  id: string
  status: Entry['status']
}

export interface LoaderDeps {
  buildApi(entry: Entry, sdkVersion: number): ExtensionApi
  importer?: (url: string) => Promise<unknown>
  runtime: Runtime
}

const str = (value: unknown): value is string => typeof value === 'string' && value.length > 0

const text = (value: unknown): string => (typeof value === 'string' ? value : '')

/** Wire shape (snake_case, from the host plugin; camelCase when read back from the cache) to `SlotDeclaration`. A malformed row is dropped. */
const parseSlot = (raw: unknown): SlotDeclaration | null => {
  const item = raw as Record<string, unknown> | null

  return item && str(item.id) && typeof item.props === 'number'
    ? {
        expectUpstream: text(item.expect_upstream ?? item.expectUpstream),
        id: item.id,
        onDrift: (item.on_drift ?? item.onDrift) === 'apply' ? 'apply' : 'skip',
        props: item.props,
        reason: text(item.reason),
        snapshotKind: (item.snapshot_kind ?? item.snapshotKind) === 'fork-core' ? 'fork-core' : 'upstream',
        upstreamCommit: text(item.upstream_commit ?? item.upstreamCommit)
      }
    : null
}

export const parseOne = (raw: unknown): ExtensionListing | null => {
  const item = raw as Partial<ExtensionListing> | null

  return item &&
    str(item.id) &&
    str(item.plugin) &&
    str(item.name) &&
    typeof item.entry === 'string' &&
    typeof item.root === 'string' &&
    typeof item.sha256 === 'string' &&
    typeof item.sdk === 'number' &&
    typeof item.granted === 'boolean'
    ? {
        entry: item.entry,
        granted: item.granted,
        id: item.id,
        name: item.name,
        plugin: item.plugin,
        reason: typeof item.reason === 'string' ? item.reason : '',
        rpc: Array.isArray(item.rpc) ? item.rpc.filter(str) : [],
        root: item.root,
        sdk: item.sdk,
        sha256: item.sha256,
        slots: Array.isArray(item.slots) ? item.slots.flatMap(slot => parseSlot(slot) ?? []) : [],
        title: typeof item.title === 'string' ? item.title : item.name
      }
    : null
}

/** Ask the host plugin. ANY failure (method not found on an older core, plugin not enabled, gateway down) is "unavailable", never an error. */
export const fetchListing = async (gw: GatewayLike): Promise<ListResult> => {
  try {
    const raw = await gw.request<{ extensions?: unknown; version?: unknown }>(LIST_METHOD, {}, 10_000)

    if (!raw || raw.version !== LISTING_VERSION || !Array.isArray(raw.extensions)) {
      return {
        detail: `host plugin answered an unknown listing (version ${String(raw?.version)}, this TUI reads ${LISTING_VERSION})`,
        ok: false
      }
    }

    return {
      listing: { extensions: raw.extensions.flatMap(item => parseOne(item) ?? []), version: LISTING_VERSION },
      ok: true
    }
  } catch (error) {
    return { detail: `host plugin not reachable: ${error instanceof Error ? error.message : String(error)}`, ok: false }
  }
}

const sha256Of = (bytes: Uint8Array): string => createHash('sha256').update(bytes).digest('hex')

const defaultImporter = (url: string): Promise<unknown> => import(/* @vite-ignore */ url)

/** Import, check and activate one verified file. The caller holds the "was loading" mark around it. */
const importAndActivate = async (deps: LoaderDeps, entry: Entry, real: string, found: string): Promise<LoadReport> => {
  const { runtime } = deps
  const listing = entry.listing
  const report = (): LoadReport => ({ detail: entry.detail, id: entry.id, status: entry.status })
  const refuse = (detail: string): LoadReport => (runtime.setStatus(entry, 'refused', detail), report())

  const importer = deps.importer ?? defaultImporter
  const imported = await runtime.guard(
    entry,
    'import',
    () => importer(`${pathToFileURL(real).href}?v=${found.slice(0, 16)}`),
    5000
  )

  if (!imported.ok) {
    runtime.setStatus(entry, 'failed', entry.errors.at(-1) ?? 'import failed')

    return report()
  }

  const raw = imported.value as { default?: unknown } | null
  const mod = (
    raw && typeof raw.default === 'object' && raw.default !== null ? raw.default : raw
  ) as Partial<ExtensionModule> | null

  if (!mod || typeof mod.activate !== 'function') {
    return refuse('the file must export activate(api) (default export or named)')
  }

  const declared = runtime.checkSdk(mod.sdkVersion)

  if (!declared.ok) {
    return refuse(declared.detail)
  }

  if (mod.sdkVersion !== listing.sdk) {
    return refuse(`plugin.yaml says SDK ${listing.sdk} but the file exports sdkVersion ${String(mod.sdkVersion)}`)
  }

  const { shim } = declared
  const version = mod.sdkVersion as number

  await runtime.activate(entry, mod as ExtensionModule, e => {
    const api = deps.buildApi(e, version)

    return shim ? shim(api) : api
  })

  return report()
}

/** Load one listed extension. Never throws: the outcome is the entry's status. */
export const loadOne = async (deps: LoaderDeps, listing: ExtensionListing): Promise<LoadReport> => {
  const { runtime } = deps
  const report = (entry: Entry): LoadReport => ({ detail: entry.detail, id: entry.id, status: entry.status })
  const existing = runtime.get(listing.id)

  // Same file and same recorded slots, still granted: nothing to reload (a re-recorded lock changes the slots, not
  // the file; a revoked grant must not keep an extension running, so it falls through to `not-granted`).
  if (
    listing.granted &&
    existing?.status === 'active' &&
    existing.listing.sha256 === listing.sha256 &&
    JSON.stringify(existing.listing.slots ?? []) === JSON.stringify(listing.slots ?? [])
  ) {
    return report(existing)
  }

  if (!listing.granted) {
    return report(runtime.track(listing, 'not-granted', listing.reason || 'not granted'))
  }

  const off = runtime.isDisabled(listing.id)

  if (off) {
    return report(runtime.track(listing, 'disabled', off))
  }

  const entry = runtime.track(listing, 'loading', '')
  const refuse = (detail: string): LoadReport => (runtime.setStatus(entry, 'refused', detail), report(entry))

  const early = runtime.checkSdk(listing.sdk)

  if (!early.ok) {
    return refuse(early.detail)
  }

  let real: string

  try {
    const [file, root] = await Promise.all([realpath(listing.entry), realpath(listing.root)])

    if (!file.startsWith(root + sep) || !['.js', '.mjs'].includes(extname(file))) {
      return refuse('entry is not a .mjs/.js file inside the plugin directory')
    }

    real = file
  } catch (error) {
    return refuse(`entry not readable (remote gateway?): ${error instanceof Error ? error.message : String(error)}`)
  }

  const found = sha256Of(await readFile(real).catch(() => new Uint8Array()))

  if (found !== listing.sha256) {
    return refuse(
      `file changed since it was listed (listed ${listing.sha256.slice(0, 8)}, found ${found.slice(0, 8)}): /extensions reload`
    )
  }

  const unmark = runtime.markLoading(listing.id)

  try {
    return await importAndActivate(deps, entry, real, found)
  } finally {
    unmark()
  }
}

/** Reconcile the registry with a fresh listing: load or reload what changed, drop what the gateway no longer lists. */
export const syncListing = async (deps: LoaderDeps, listing: Listing): Promise<LoadReport[]> => {
  const keep = new Set(listing.extensions.map(item => item.id))

  for (const entry of deps.runtime.entries()) {
    if (!keep.has(entry.id)) {
      deps.runtime.untrack(entry.id)
    }
  }

  const reports: LoadReport[] = []

  for (const item of listing.extensions) {
    reports.push(await loadOne(deps, item))
  }

  return reports
}
