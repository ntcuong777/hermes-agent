import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { createApi } from '../../sdk/ext/api.js'
import type { ExtensionListing } from '../../sdk/ext/contract.js'
import { fetchListing, loadOne, syncListing } from '../../sdk/ext/loader.js'
import { createRuntime } from '../../sdk/ext/runtime.js'
import { memoryStore } from '../../sdk/ext/store.js'

const GOOD = `export default {
  sdkVersion: 1,
  activate(api) {
    api.hooks.on('t.transform', p => ({ text: p.text + '!' }))
    api.registerCommand('hi', { help: 'x', run: (a, ctx) => ctx.print('hi ' + a) })
  }
}
`

const sha = (text: string) => createHash('sha256').update(text).digest('hex')

/** A plugin dir with tui/<name>.mjs and the listing entry the host plugin would produce for it. */
const plugin = (source: string, over: Partial<ExtensionListing> = {}, name = 'a') => {
  const root = mkdtempSync(join(tmpdir(), 'ext-plugin-'))

  mkdirSync(join(root, 'tui'))
  writeFileSync(join(root, 'tui', `${name}.mjs`), source)

  const listing: ExtensionListing = {
    entry: join(root, 'tui', `${name}.mjs`),
    granted: true,
    id: `p/${name}`,
    name,
    plugin: 'p',
    reason: '',
    rpc: [],
    root,
    sdk: 1,
    sha256: sha(source),
    title: name,
    ...over
  }

  return { listing, root }
}

const setup = (importer?: (url: string) => Promise<unknown>) => {
  const notices: string[] = []
  const store = memoryStore()
  const runtime = createRuntime({
    hooks: { 't.transform': { mode: 'transform' } },
    notify: t => void notices.push(t),
    store
  })

  const deps = {
    buildApi: (entry: any, version: number) =>
      createApi(runtime, entry, version, {
        gw: { request: async () => ({}) as never },
        log: () => {},
        notify: () => {},
        sdk: {},
        state: { get: () => ({}) as never, subscribe: () => () => {} }
      }),
    importer,
    runtime
  }

  return { deps, notices, runtime, store }
}

describe('loadOne', () => {
  it('imports a granted extension from disk, activates it and serves its hook and command', async () => {
    const { deps, runtime } = setup()
    const { listing } = plugin(GOOD)

    expect(await loadOne(deps, listing)).toEqual({ detail: '', id: 'p/a', status: 'active' })
    expect(await runtime.run('t.transform', { text: 'a' })).toEqual({ text: 'a!' })
    expect(await runtime.runCommand('hi', 'you', () => {})).toBe(true)
  })

  it('accepts named exports instead of a default export', async () => {
    const { deps } = setup()
    const { listing } = plugin('export const sdkVersion = 1\nexport function activate() {}\n')

    expect((await loadOne(deps, listing)).status).toBe('active')
  })

  it('does not import an extension that is not granted, and says why', async () => {
    const importer = vi.fn()
    const { deps } = setup(importer)
    const { listing } = plugin(GOOD, { granted: false, reason: 'set tui.extensions.p.allow: true' })

    expect(await loadOne(deps, listing)).toEqual({
      detail: 'set tui.extensions.p.allow: true',
      id: 'p/a',
      status: 'not-granted'
    })
    expect(importer).not.toHaveBeenCalled()
  })

  it('does not import a disabled extension', async () => {
    const importer = vi.fn()
    const { deps, store } = setup(importer)
    const { listing } = plugin(GOOD)

    store.write({ 'p/a': 'hung the TUI for over 5000ms (watchdog)' })

    expect(await loadOne(deps, listing)).toEqual({
      detail: 'hung the TUI for over 5000ms (watchdog)',
      id: 'p/a',
      status: 'disabled'
    })
    expect(importer).not.toHaveBeenCalled()
  })

  it('marks the extension as loading while it imports and clears the mark afterwards, on success and on failure', async () => {
    let during: Record<string, string> = {}
    const ctx = setup(url => {
      during = ctx.store.read()

      return import(/* @vite-ignore */ url)
    })

    const good = plugin(GOOD, {}, 'a')
    const broken = plugin('export default {', {}, 'b')

    expect((await loadOne(ctx.deps, good.listing)).status).toBe('active')
    expect(during).toEqual({
      'p/a': 'was loading when the TUI stopped (hang or crash): retry with /extensions enable p/a'
    })
    expect(ctx.store.read()).toEqual({})

    expect((await loadOne(ctx.deps, broken.listing)).status).toBe('failed')
    expect(ctx.store.read()).toEqual({})
  })

  it('a mark left behind by a killed process keeps the extension from loading at the next start', async () => {
    const importer = vi.fn()
    const { deps, store } = setup(importer)
    const { listing } = plugin(GOOD)

    store.write({ 'p/a': 'was loading when the TUI stopped (hang or crash): retry with /extensions enable p/a' })

    expect(await loadOne(deps, listing)).toMatchObject({ id: 'p/a', status: 'disabled' })
    expect(importer).not.toHaveBeenCalled()
  })

  it('refuses a newer SDK before importing anything', async () => {
    const importer = vi.fn()
    const { deps } = setup(importer)
    const { listing } = plugin(GOOD, { sdk: 9 })

    expect(await loadOne(deps, listing)).toMatchObject({
      detail: 'needs SDK 9, this TUI has SDK 1: update the TUI (nix flake update hermes, rebuild)',
      status: 'refused'
    })
    expect(importer).not.toHaveBeenCalled()
  })

  it('refuses a file whose bytes no longer match the listed sha256', async () => {
    const importer = vi.fn()
    const { deps } = setup(importer)
    const { listing } = plugin(GOOD, { sha256: 'f'.repeat(64) })

    const report = await loadOne(deps, listing)

    expect(report.status).toBe('refused')
    expect(report.detail).toMatch(
      /^file changed since it was listed \(listed ffffffff, found [0-9a-f]{8}\): \/extensions reload$/
    )
    expect(importer).not.toHaveBeenCalled()
  })

  it('refuses an entry that leaves the plugin directory (symlink) or is not .mjs/.js', async () => {
    const { deps } = setup()
    const { listing: outside } = plugin(GOOD, {}, 'outside')
    const { listing, root } = plugin(GOOD, {}, 'inside')

    symlinkSync(outside.entry, join(root, 'tui', 'link.mjs'))
    writeFileSync(join(root, 'tui', 'x.txt'), GOOD)

    expect(await loadOne(deps, { ...listing, entry: join(root, 'tui', 'link.mjs') })).toMatchObject({
      detail: 'entry is not a .mjs/.js file inside the plugin directory',
      status: 'refused'
    })
    expect(await loadOne(deps, { ...listing, entry: join(root, 'tui', 'x.txt'), id: 'p/x' })).toMatchObject({
      status: 'refused'
    })
  })

  it('refuses a missing entry file (a remote gateway lists paths this machine does not have)', async () => {
    const { deps } = setup()
    const { listing } = plugin(GOOD)

    const report = await loadOne(deps, { ...listing, entry: join(listing.root, 'tui', 'gone.mjs') })

    expect(report.status).toBe('refused')
    expect(report.detail).toMatch(/^entry not readable \(remote gateway\?\)/)
  })

  it('refuses a file that does not export activate, or whose sdkVersion disagrees with plugin.yaml', async () => {
    const { deps } = setup()
    const none = plugin('export default 42\n', {}, 'none')
    const skew = plugin('export default { sdkVersion: 3, activate() {} }\n', {}, 'skew')

    expect(await loadOne(deps, none.listing)).toMatchObject({
      detail: 'the file must export activate(api) (default export or named)',
      status: 'refused'
    })
    expect(await loadOne(deps, skew.listing)).toMatchObject({
      detail: 'needs SDK 3, this TUI has SDK 1: update the TUI (nix flake update hermes, rebuild)',
      status: 'refused'
    })
  })

  it('a syntax error or a throwing activate is a failed extension, never an exception', async () => {
    const { deps, runtime } = setup()
    const broken = plugin('export default {', {}, 'broken')
    const throws = plugin('export default { sdkVersion: 1, activate() { throw new Error("no") } }\n', {}, 'throws')

    expect((await loadOne(deps, broken.listing)).status).toBe('failed')
    expect(await loadOne(deps, throws.listing)).toMatchObject({ status: 'failed' })
    expect(runtime.get('p/throws')?.errors[0]).toMatch(/activate: no$/)
  })

  it('is idempotent for an unchanged active extension and re-imports a changed one', async () => {
    const importer = vi.fn((url: string) => import(/* @vite-ignore */ url))
    const { deps } = setup(importer)
    const { listing, root } = plugin(GOOD)

    await loadOne(deps, listing)
    await loadOne(deps, listing)
    expect(importer).toHaveBeenCalledTimes(1)

    const next = GOOD.replace("'!'", "'?'")

    writeFileSync(listing.entry, next)
    expect((await loadOne(deps, { ...listing, root, sha256: sha(next) })).status).toBe('active')
    expect(importer).toHaveBeenCalledTimes(2)
    expect(importer.mock.calls[1]![0]).toContain(`?v=${sha(next).slice(0, 16)}`)
  })
})

describe('fetchListing', () => {
  const gw = (answer: () => Promise<unknown>) => ({ request: answer as never })

  it('any failure is "unavailable", with the reason', async () => {
    expect(await fetchListing(gw(() => Promise.reject(new Error('method not found'))))).toEqual({
      detail: 'host plugin not reachable: method not found',
      ok: false
    })
  })

  it('refuses a listing of another shape version', async () => {
    expect(await fetchListing(gw(async () => ({ extensions: [], version: 2 })))).toEqual({
      detail: 'host plugin answered an unknown listing (version 2, this TUI reads 1)',
      ok: false
    })
  })

  it('keeps a not-granted row that has no file (empty entry and hash) so /extensions can explain it', async () => {
    const row = {
      entry: '',
      granted: false,
      id: 'p/x',
      name: 'x',
      plugin: 'p',
      reason: 'entry file not found',
      rpc: [],
      root: '/p',
      sdk: 1,
      sha256: '',
      title: 'x'
    }
    const result = await fetchListing(gw(async () => ({ extensions: [row], version: 1 })))

    expect(result).toEqual({ listing: { extensions: [{ ...row, slots: [] }], version: 1 }, ok: true })
  })

  it('keeps well-formed entries and drops malformed ones', async () => {
    const { listing } = plugin(GOOD)
    const result = await fetchListing(gw(async () => ({ extensions: [listing, { id: 'broken' }, null], version: 1 })))

    expect(result).toEqual({ listing: { extensions: [{ ...listing, slots: [] }], version: 1 }, ok: true })
  })
})

describe('syncListing', () => {
  it('loads what is listed and forgets what the gateway stopped listing', async () => {
    const { deps, runtime } = setup()
    const a = plugin(GOOD, {}, 'a')
    const b = plugin(GOOD.replace("'hi'", "'ho'"), {}, 'b')

    await syncListing(deps, { extensions: [a.listing, b.listing], version: 1 })
    expect(runtime.entries().map(e => `${e.id} ${e.status}`)).toEqual(['p/a active', 'p/b active'])

    const reports = await syncListing(deps, { extensions: [a.listing], version: 1 })

    expect(reports.map(r => r.id)).toEqual(['p/a'])
    expect(runtime.entries().map(e => e.id)).toEqual(['p/a'])
    expect(runtime.commandOwner('ho')).toBeUndefined()
  })
})
