import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { HOOKS } from '../../sdk/ext/contract.js'
import { clipboardImagePathFromExtensions, transformPasteText } from '../../sdk/ext/composerHooks.js'
import { createHost, type Host } from '../../sdk/ext/host.js'
import { memoryStore } from '../../sdk/ext/store.js'

const sha = (text: string) => createHash('sha256').update(text).digest('hex')

/** A real extension file per entry, listed the way the host plugin would list it. */
const rig = async (sources: Record<string, string>): Promise<Host> => {
  const rows = Object.entries(sources).map(([id, source]) => {
    const root = mkdtempSync(join(tmpdir(), 'ext-hooks-'))
    const [plugin, name] = id.split('/') as [string, string]

    mkdirSync(join(root, 'tui'))
    writeFileSync(join(root, 'tui', `${name}.mjs`), source)

    return {
      entry: join(root, 'tui', `${name}.mjs`),
      granted: true,
      id,
      name,
      plugin,
      reason: '',
      rpc: [],
      root,
      sdk: 1,
      sha256: sha(source),
      title: name
    }
  })

  const host = createHost()

  host.attach({
    gw: { request: async <T>() => ({ extensions: rows, version: 1 }) as T },
    log: () => {},
    notify: () => {},
    sdk: {},
    state: { get: () => ({}) as never, subscribe: () => () => {} },
    store: memoryStore()
  })
  await host.refresh()

  return host
}

const ext = (body: string) => `export default { sdkVersion: 1, activate(api) { ${body} } }\n`

describe('the hook table', () => {
  it('defines the composer hooks and the gateway.event hook', () => {
    expect(HOOKS).toEqual({
      'composer.clipboardImage': { mode: 'first' },
      'composer.paste': { mode: 'transform' },
      'gateway.event': { mode: 'all' }
    })
  })
})

describe('composer.paste', () => {
  it('without extensions the text is untouched and there is nothing to call', async () => {
    const host = await rig({})

    expect(host.has('composer.paste')).toBe(false)
    expect(await transformPasteText('a\nb', true, host)).toBe('a\nb')
  })

  it('extensions transform in id order and see the bracketed flag', async () => {
    const host = await rig({
      'a/flag': ext("api.hooks.on('composer.paste', p => ({ text: p.text + (p.bracketed ? ' [b]' : ' [k]') }))"),
      'b/upper': ext("api.hooks.on('composer.paste', p => ({ text: p.text.toUpperCase() }))")
    })

    expect(host.has('composer.paste')).toBe(true)
    expect(await transformPasteText('hi', true, host)).toBe('HI [B]')
    expect(await transformPasteText('hi', false, host)).toBe('HI [K]')
  })

  it('null swallows the paste; a broken handler is skipped and the text survives', async () => {
    const host = await rig({
      'a/broken': ext("api.hooks.on('composer.paste', () => { throw new Error('x') })"),
      'b/swallow': ext("api.hooks.on('composer.paste', p => (p.text.includes('secret') ? null : undefined))")
    })

    expect(await transformPasteText('plain', false, host)).toBe('plain')
    expect(await transformPasteText('my secret', false, host)).toBeNull()
  })

  it('a handler that returns garbage cannot corrupt the text', async () => {
    const host = await rig({ 'a/bad': ext("api.hooks.on('composer.paste', () => ({ text: 42 }))") })

    expect(await transformPasteText('keep', false, host)).toBe('keep')
  })
})

describe('composer.clipboardImage', () => {
  it('no extension means null', async () => {
    expect(await clipboardImagePathFromExtensions(await rig({}))).toBeNull()
  })

  it('the first extension that stages a file wins; a throwing or empty one is skipped', async () => {
    const host = await rig({
      'a/throws': ext("api.hooks.on('composer.clipboardImage', () => { throw new Error('no osascript') })"),
      'b/none': ext("api.hooks.on('composer.clipboardImage', async () => null)"),
      'c/stages': ext("api.hooks.on('composer.clipboardImage', async () => '/tmp/clip_1.png')"),
      'd/later': ext("api.hooks.on('composer.clipboardImage', () => '/tmp/never.png')")
    })

    expect(await clipboardImagePathFromExtensions(host)).toBe('/tmp/clip_1.png')
  })

  it('anything that is not a non-empty string is ignored', async () => {
    const host = await rig({ 'a/odd': ext("api.hooks.on('composer.clipboardImage', () => ({ path: 'x' }))") })

    expect(await clipboardImagePathFromExtensions(host)).toBeNull()
  })
})
