import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { runExtCommand, runExtensionsCommand } from '../../sdk/ext/command.js'
import type { SlotDef } from '../../sdk/ext/contract.js'
import { createHost } from '../../sdk/ext/host.js'
import { memoryStore } from '../../sdk/ext/store.js'
import { createFakeHost } from '../../sdk/ext/testing.js'

const HELLO = `export default {
  sdkVersion: 1,
  activate(api) {
    api.registerCommand('hello', { help: 'say hello', run: (a, ctx) => ctx.print('hello ' + (a || 'world') + ' sid=' + api.state.get().sid) })
  }
}
`

const sha = (text: string) => createHash('sha256').update(text).digest('hex')

const listingFor = (root: string, source: string) => ({
  entry: join(root, 'tui', 'hello.mjs'),
  granted: true,
  id: 'demo/hello',
  name: 'hello',
  plugin: 'demo',
  reason: '',
  rpc: [],
  root,
  sdk: 1,
  sha256: sha(source),
  title: 'Hello'
})

const rig = () => {
  const root = mkdtempSync(join(tmpdir(), 'ext-cmd-'))

  mkdirSync(join(root, 'tui'))
  writeFileSync(join(root, 'tui', 'hello.mjs'), HELLO)

  const state = { listing: [listingFor(root, HELLO)] as unknown[], reply: null as null | Error }
  const notices: string[] = []
  const store = memoryStore()
  const host = createHost()

  host.attach({
    gw: {
      request: async <T>() => {
        if (state.reply) {
          throw state.reply
        }

        return { extensions: state.listing, version: 1 } as T
      }
    },
    log: () => {},
    notify: text => void notices.push(text),
    sdk: {},
    state: { get: () => ({ sid: 'S1' }) as never, subscribe: () => () => {} },
    store
  })

  return { host, notices, root, state, store }
}

afterEach(() => {
  delete process.env.HERMES_TUI_EXTENSIONS
})

describe('/extensions and /ext', () => {
  it('lists, runs a command, and reports unknown ones', async () => {
    const { host } = rig()

    await host.refresh()

    expect(await runExtensionsCommand(host, '')).toEqual(['active      demo/hello  sdk 1  hooks 0  commands 1'])
    expect(await runExtCommand(host, '')).toEqual(['/ext hello  say hello  (demo/hello)'])
    expect(await runExtCommand(host, 'hello team')).toEqual(['hello team sid=S1'])
    expect(await runExtCommand(host, 'nope')).toEqual(["unknown extension command 'nope' (/ext lists them)"])
  })

  it('disable persists and unloads; enable loads it again', async () => {
    const { host, store } = rig()

    await host.refresh()
    expect(await runExtensionsCommand(host, 'disable demo/hello')).toEqual([
      'demo/hello disabled (persisted; /extensions enable demo/hello to undo)'
    ])
    expect(store.read()).toEqual({ 'demo/hello': 'disabled with /extensions disable' })
    expect(await runExtCommand(host, 'hello')).toEqual(["unknown extension command 'hello' (/ext lists them)"])

    await host.refresh()
    expect((await runExtensionsCommand(host, 'list'))[0]).toMatch(
      /^disabled    demo\/hello .*disabled with \/extensions disable$/
    )

    expect(await runExtensionsCommand(host, 'enable demo/hello')).toEqual(['active      demo/hello'])
    expect(store.read()).toEqual({})
    expect(await runExtCommand(host, 'hello')).toEqual(['hello world sid=S1'])
  })

  it('reload picks up an edited file; refresh alone refuses a file that changed under the old listing', async () => {
    const { host, root, state } = rig()

    await host.refresh()

    const edited = HELLO.replace("'hello '", "'howdy '")

    writeFileSync(join(root, 'tui', 'hello.mjs'), edited)

    const stale = await host.refresh()

    expect(stale).toEqual([{ detail: '', id: 'demo/hello', status: 'active' }])

    state.listing = [listingFor(root, edited)]
    expect(await runExtensionsCommand(host, 'reload demo/hello')).toEqual(['active      demo/hello'])
    expect(await runExtCommand(host, 'hello')).toEqual(['howdy world sid=S1'])
  })

  it('info and errors', async () => {
    const { host, root } = rig()

    await host.refresh()

    expect(await runExtensionsCommand(host, 'info demo/hello')).toEqual([
      'active      demo/hello  sdk 1  hooks 0  commands 1',
      'title: Hello',
      `file: ${join(root, 'tui', 'hello.mjs')}`,
      `sha256: ${sha(HELLO)}`,
      'rpc: plugin.demo.*'
    ])
    expect(await runExtensionsCommand(host, 'info nope')).toEqual(['no extension nope'])
    expect(await runExtensionsCommand(host, 'errors')).toEqual(['no errors recorded'])
    expect(await runExtensionsCommand(host, 'help')).toHaveLength(9)
  })

  it('says why it is idle when the host plugin is not there, and when the env switch is off', async () => {
    const { host, state } = rig()

    state.reply = new Error('method not found')
    await host.refresh()
    expect(await runExtensionsCommand(host, '')).toEqual([
      'no extensions',
      '(host plugin not reachable: method not found)'
    ])

    process.env.HERMES_TUI_EXTENSIONS = '0'

    const off = createHost()

    off.attach({
      gw: { request: async () => ({}) as never },
      log: () => {},
      notify: () => {},
      sdk: {},
      state: { get: () => ({}) as never, subscribe: () => () => {} }
    })
    expect(await runExtensionsCommand(off, '')).toEqual(['TUI extensions are off: HERMES_TUI_EXTENSIONS=0'])
    expect(await off.runHook('composer.paste', 'same')).toBe('same')
  })

  it('a host that was never attached is inert', async () => {
    const host = createHost()

    expect(host.has('x')).toBe(false)
    expect(await host.runHook('x', 1)).toBe(1)
    expect(await host.refresh()).toEqual([])
    expect(await runExtCommand(host, 'hello')).toEqual(["unknown extension command 'hello' (/ext lists them)"])
  })
})

describe('/extensions slots (drift marker)', () => {
  const slotDef: SlotDef = { file: 'ui-tui/src/x.ts', host: [], kind: 'fn', props: 1, summary: 'x', symbols: ['x'] }
  const declared = (id: string) => ({
    expectUpstream: 'a'.repeat(64),
    id,
    props: 1,
    reason: 'why it exists',
    upstreamCommit: '0123456789abcdef'
  })

  it('marks a drifted mounted slot DRIFTED and leaves the clean one unmarked', async () => {
    const h = createFakeHost({
      slotDefs: { 't.clean': slotDef, 't.drift': slotDef },
      upstream: { 't.clean': 'a'.repeat(64), 't.drift': 'c'.repeat(64) }
    })

    await h.activate(
      {
        activate: (api: any) => ['t.drift', 't.clean'].forEach(id => api.slots.register(id, () => 'ext')),
        sdkVersion: 1
      },
      { slots: [declared('t.drift'), declared('t.clean')] }
    )

    const lines = await runExtensionsCommand({ idleReason: () => '', runtime: () => h.runtime } as never, 'slots')
    const drifted = lines.find(line => line.includes('t.drift'))
    const clean = lines.find(line => line.includes('t.clean'))

    expect(drifted).toMatch(/^active DRIFTED\s+t\.drift\s/)
    expect(clean).toMatch(/^active\s+t\.clean\s/)
    expect(clean).not.toContain('DRIFTED')
  })
})
