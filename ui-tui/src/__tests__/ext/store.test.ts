import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { defaultCachePath } from '../../sdk/ext/listingCache.js'
import { defaultStatusDir } from '../../sdk/ext/slotStatus.js'
import { defaultStatePath, fileStore, memoryStore } from '../../sdk/ext/store.js'

describe('stores', () => {
  it('defaultStatePath follows HERMES_HOME', () => {
    expect(defaultStatePath({ HERMES_HOME: '/h' })).toBe('/h/tui-extensions.json')
  })

  it('under vitest without HERMES_HOME every default path is a temp one, never the real ~/.hermes', () => {
    const temp = join(tmpdir(), 'hermes-vitest-home')

    expect(defaultStatePath({ VITEST: 'true' })).toBe(join(temp, 'tui-extensions.json'))
    expect(defaultCachePath({ VITEST: 'true' })).toBe(join(temp, 'tui-extensions-cache.json'))
    expect(defaultStatusDir({ VITEST: 'true' })).toBe(join(temp, 'tui-slots'))
    expect(defaultStatePath({ HERMES_HOME: '/h', VITEST: 'true' })).toBe('/h/tui-extensions.json')
    expect(defaultStatePath({})).toMatch(/\.hermes\/tui-extensions\.json$/)
  })

  it('fileStore round-trips, reads a missing or corrupt file as empty, and never throws on write', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ext-store-'))
    const path = join(dir, 'nested', 'tui-extensions.json')
    const store = fileStore(path)

    expect(store.read()).toEqual({})
    store.write({ 'p/a': 'because' })
    expect(store.read()).toEqual({ 'p/a': 'because' })
    expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual({ disabled: { 'p/a': 'because' } })

    writeFileSync(path, 'not json')
    expect(store.read()).toEqual({})
    expect(() => fileStore('/proc/definitely/not/writable/x.json').write({ a: 'b' })).not.toThrow()
  })

  it('memoryStore copies its input and output', () => {
    const initial = { a: '1' }
    const store = memoryStore(initial)

    initial.a = 'changed'
    expect(store.read()).toEqual({ a: '1' })
    store.read().a = 'mutated'
    expect(store.read()).toEqual({ a: '1' })
  })
})
