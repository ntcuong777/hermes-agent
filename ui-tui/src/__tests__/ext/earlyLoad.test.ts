import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import type { SlotDef } from '../../sdk/ext/contract.js'
import { primeExtensionSlots } from '../../sdk/ext/earlyLoad.js'
import { createHost, type HostDeps } from '../../sdk/ext/host.js'
import { nullStatusSink } from '../../sdk/ext/slotStatus.js'
import { memoryStore } from '../../sdk/ext/store.js'

const HASH = 'a'.repeat(64)

const DEFS: Record<string, SlotDef> = {
  't.fn': { file: 'ui-tui/src/x.ts', host: [], kind: 'fn', props: 1, summary: 'x', symbols: ['x'] }
}

const SOURCE = `export default { sdkVersion: 1, activate(api) { api.slots.register('t.fn', () => 'from the extension') } }\n`
const sha = (text: string) => createHash('sha256').update(text).digest('hex')

const wire = (root: string, entry: string, source: string, granted = true) => ({
  entry,
  granted,
  id: 'p/x',
  name: 'x',
  plugin: 'p',
  reason: '',
  rpc: [],
  root,
  sdk: 1,
  sha256: sha(source),
  slots: [{ expect_upstream: HASH, id: 't.fn', props: 1, reason: 'why', upstream_commit: 'abcdef0123456789' }],
  title: 'x'
})

const setup = (rows: (root: string, entry: string) => unknown[] | null) => {
  const root = mkdtempSync(join(tmpdir(), 'early-'))

  mkdirSync(join(root, 'plugin', 'tui'), { recursive: true })

  const entry = join(root, 'plugin', 'tui', 'x.mjs')

  writeFileSync(entry, SOURCE)

  const cachePath = join(root, 'cache.json')
  const built = rows(join(root, 'plugin'), entry)

  if (built) {
    writeFileSync(cachePath, JSON.stringify({ extensions: built, version: 1 }))
  }

  const logs: string[] = []
  const live: string[] = []

  const deps = (over: Partial<HostDeps> = {}): HostDeps => ({
    cachePath,
    gw: { request: async () => Promise.reject(new Error('gateway not up')) },
    log: line => void logs.push(line),
    notify: text => void live.push(text),
    sdk: {},
    slotDefs: DEFS,
    state: { get: () => ({}) as never, subscribe: () => () => {} },
    status: nullStatusSink,
    store: memoryStore(),
    upstream: { 't.fn': HASH },
    ...over
  })

  return { deps, entry, live, logs, root }
}

describe('primeExtensionSlots', () => {
  it('mounts the slot extensions of the cached listing before it returns, with the gateway down', async () => {
    const t = setup((root, entry) => [wire(root, entry, SOURCE)])
    const host = createHost()

    await primeExtensionSlots({ request: async () => Promise.reject(new Error('down')) } as never, host, 1500, () =>
      t.deps()
    )

    expect(host.slotFor('t.fn')?.extId).toBe('p/x')
    expect(
      t.logs.some(line => line.includes('tui slot t.fn active: extension p/x written against upstream abcdef0123'))
    ).toBe(true)
  })

  it('does nothing without a cache, with an ungranted row, or with a row that has no slots', async () => {
    for (const rows of [
      () => null,
      (root: string, entry: string) => [wire(root, entry, SOURCE, false)],
      (root: string, entry: string) => [{ ...wire(root, entry, SOURCE), slots: [] }]
    ]) {
      const t = setup(rows)
      const host = createHost()

      await primeExtensionSlots({} as never, host, 1500, () => t.deps())
      expect(host.slotFor('t.fn')).toBeUndefined()
    }
  })

  it('a changed file is refused (the cache is only a hint), and the live listing can still load it later', async () => {
    const t = setup((root, entry) => [wire(root, entry, `${SOURCE}// old\n`)])
    const host = createHost()

    await primeExtensionSlots({} as never, host, 1500, () => t.deps())

    expect(host.slotFor('t.fn')).toBeUndefined()
    expect(host.runtime()?.get('p/x')?.status).toBe('refused')
  })

  it('returns within the budget when an import never settles; nothing is mounted yet', async () => {
    const t = setup((root, entry) => [wire(root, entry, SOURCE)])
    const host = createHost()
    const started = Date.now()

    await primeExtensionSlots({} as never, host, 80, () => t.deps({ importer: () => new Promise(() => {}) }))

    expect(Date.now() - started).toBeLessThan(1000)
    expect(host.slotFor('t.fn')).toBeUndefined()
  })

  it('holds notices raised early until the UI attaches, then delivers them', async () => {
    const t = setup((root, entry) => [wire(root, entry, SOURCE)])
    const host = createHost()

    await primeExtensionSlots({} as never, host, 1500, () => t.deps({ upstream: { 't.fn': 'b'.repeat(64) } }))
    expect(t.live).toEqual([])

    host.attach(t.deps())

    expect(t.live).toHaveLength(1)
    expect(t.live[0]).toContain('slot t.fn skipped-drift')
    expect(host.slotFor('t.fn')).toBeUndefined()
  })

  it('the second attach keeps the mounted slots and the live listing then refreshes and caches', async () => {
    const t = setup((root, entry) => [wire(root, entry, SOURCE)])
    const host = createHost()

    await primeExtensionSlots({} as never, host, 1500, () => t.deps())

    const listed = { extensions: [wire(join(t.root, 'plugin'), t.entry, SOURCE)], version: 1 }

    host.attach(t.deps({ gw: { request: async () => listed as never } }))
    expect(host.slotFor('t.fn')).toBeDefined()

    const reports = await host.refresh()

    expect(reports).toEqual([{ detail: '', id: 'p/x', status: 'active' }])
    expect(host.slotFor('t.fn')).toBeDefined()
  })

  it('a revoked grant in the live listing unmounts the slot at once', async () => {
    const t = setup((root, entry) => [wire(root, entry, SOURCE)])
    const host = createHost()

    await primeExtensionSlots({} as never, host, 1500, () => t.deps())

    const revoked = { extensions: [wire(join(t.root, 'plugin'), t.entry, SOURCE, false)], version: 1 }

    host.attach(t.deps({ gw: { request: async () => revoked as never } }))
    await host.refresh()

    expect(host.slotFor('t.fn')).toBeUndefined()
    expect(host.slotRows()[0]).toMatchObject({ id: 't.fn', status: 'skipped-no-grant' })
  })
})
