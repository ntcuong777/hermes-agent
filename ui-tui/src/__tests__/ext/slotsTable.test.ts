/** slots.json against the tree: a renamed or removed upstream symbol fails here before any extension is involved. */
import { existsSync, readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { canaryEcho, echo } from '../../sdk/ext/canary.js'
import { HOST_MODULES } from '../../sdk/ext/hostModules.js'
import { regionText } from '../../sdk/ext/slotRegion.mjs'
import { slotFn } from '../../sdk/ext/slotReact.js'
import { SLOT_DEFS } from '../../sdk/ext/slots.js'
import { createFakeHost } from '../../sdk/ext/testing.js'

const root = new URL('../../../../', import.meta.url)

describe('slots.json', () => {
  it.each(Object.entries(SLOT_DEFS))('%s: its file exists and declares every symbol of the region', (id, def) => {
    const file = new URL(def.file, root)

    expect(existsSync(file), `${def.file} is missing`).toBe(true)
    expect(() => regionText(readFileSync(file, 'utf8'), def.symbols), id).not.toThrow()
    expect(['component', 'fn', 'hook']).toContain(def.kind)
    expect(Number.isInteger(def.props) && def.props >= 1).toBe(true)
  })

  it.each(Object.entries(SLOT_DEFS))('%s: every host module it lists is exposed by this TUI', (_id, def) => {
    expect(def.host.filter(id => !(id in HOST_MODULES))).toEqual([])
  })

  it('the two packages are always exposed', () => {
    expect(Object.keys(HOST_MODULES)).toEqual(expect.arrayContaining(['ink', 'react']))
  })
})

describe('the canary slot', () => {
  it('is the stock function until an extension mounts canary.echo', async () => {
    expect(echo('hi')).toBe('hi')

    const h = createFakeHost({ upstream: { 'canary.echo': 'a'.repeat(64) } })

    await h.activate(
      { activate: (api: any) => api.slots.register('canary.echo', (t: string) => `${t}!`), sdkVersion: 1 },
      {
        slots: [
          { expectUpstream: 'a'.repeat(64), id: 'canary.echo', props: 1, reason: 'test', upstreamCommit: 'abcdef0123' }
        ]
      }
    )

    expect(slotFn('canary.echo', canaryEcho, h.host)('hi')).toBe('hi!')
  })
})
