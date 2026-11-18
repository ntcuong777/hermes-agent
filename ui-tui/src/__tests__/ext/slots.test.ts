import { describe, expect, it } from 'vitest'

import type { SlotDeclaration, SlotDef } from '../../sdk/ext/contract.js'
import { createRuntime } from '../../sdk/ext/runtime.js'
import { checkSlot } from '../../sdk/ext/slots.js'
import { createFakeHost } from '../../sdk/ext/testing.js'
import { memoryStore } from '../../sdk/ext/store.js'

const def = (over: Partial<SlotDef> = {}): SlotDef => ({
  file: 'ui-tui/src/x.ts',
  host: [],
  kind: 'fn',
  props: 1,
  summary: 'x',
  symbols: ['x'],
  ...over
})

const DEFS = { 't.c': def({ kind: 'component' }), 't.fn': def(), 't.other': def() }
const HASH = 'a'.repeat(64)

const decl = (id: string, over: Partial<SlotDeclaration> = {}): SlotDeclaration => ({
  expectUpstream: HASH,
  id,
  props: 1,
  reason: 'why it exists',
  upstreamCommit: '0123456789abcdef',
  ...over
})

const ext = (ids: string[], impl: unknown = () => 'ext') => ({
  activate: (api: any) => ids.forEach(id => api.slots.register(id, impl)),
  sdkVersion: 1
})

const host = (over: Record<string, unknown> = {}) =>
  createFakeHost({ slotDefs: DEFS, upstream: { 't.c': HASH, 't.fn': HASH, 't.other': HASH }, ...over })

function baseListing() {
  return {
    entry: '/fake/ext.mjs',
    granted: true,
    id: 'fake/ext',
    name: 'ext',
    plugin: 'fake',
    reason: '',
    rpc: [],
    root: '/fake',
    sdk: 1,
    sha256: '0'.repeat(64),
    slots: [] as SlotDeclaration[],
    title: 'ext'
  }
}

describe('checkSlot', () => {
  const upstream = { 't.fn': HASH }

  it('unknown id, props mismatch, missing provenance, missing build hash, drift, ok: in that order', () => {
    expect(checkSlot(decl('zzz'), DEFS, upstream)).toMatchObject({ status: 'skipped-error' })
    expect(checkSlot(decl('t.fn', { props: 2 }), DEFS, upstream)).toMatchObject({ status: 'skipped-incompatible' })
    expect(checkSlot(decl('t.fn', { upstreamCommit: '' }), DEFS, upstream).detail).toContain('no provenance')
    expect(checkSlot(decl('t.fn', { reason: '' }), DEFS, upstream).status).toBe('skipped-error')
    expect(checkSlot(decl('t.other'), DEFS, upstream).detail).toContain('no upstream hash')

    const drift = checkSlot(decl('t.fn', { expectUpstream: 'b'.repeat(64), onDrift: 'skip' }), DEFS, upstream)

    expect(drift.status).toBe('skipped-drift')
    expect(drift.detail).toContain('ui-tui/src/x.ts changed since 0123456789')
    expect(drift.detail).toContain('tui-slot-check --agent-brief t.fn')
    expect(checkSlot(decl('t.fn'), DEFS, upstream)).toEqual({ detail: '', status: 'active' })
  })

  it('on_drift apply (the default): a changed region is active and drifted, with the same detail; hard cases stay skipped', () => {
    const upstream = { 't.fn': HASH }
    const changed = { expectUpstream: 'b'.repeat(64) }

    for (const given of [decl('t.fn', changed), decl('t.fn', { ...changed, onDrift: 'apply' })]) {
      const verdict = checkSlot(given, DEFS, upstream)

      expect(verdict).toMatchObject({ drifted: true, status: 'active' })
      expect(verdict.detail).toContain('ui-tui/src/x.ts changed since 0123456789')
      expect(verdict.detail).toContain('tui-slot-check --agent-brief t.fn')
      expect(verdict.detail).toContain('mounted anyway (on_drift apply)')
    }

    expect(checkSlot(decl('zzz', changed), DEFS, upstream).status).toBe('skipped-error')
    expect(checkSlot(decl('t.fn', { ...changed, props: 2 }), DEFS, upstream).status).toBe('skipped-incompatible')
    expect(checkSlot(decl('t.other', changed), DEFS, upstream).detail).toContain('no upstream hash')
  })
})

describe('registering slots', () => {
  it('mounts a declared slot and serves it by id; one log line names slot, extension and upstream commit', async () => {
    const h = host()
    const entry = await h.activate(ext(['t.fn']), { slots: [decl('t.fn')] })

    expect(entry.status).toBe('active')
    expect(h.runtime.slotFor('t.fn')?.extId).toBe('fake/ext')
    expect(h.runtime.slotFor('t.c')).toBeUndefined()
    expect(h.logs).toContain(
      'tui slot t.fn active: extension fake/ext written against upstream 0123456789 (why it exists)'
    )
    expect(h.slotEvents).toMatchObject([{ extId: 'fake/ext', kind: 'applied', slot: 't.fn', status: 'active' }])
  })

  it('an undeclared slot, an unknown id, a non-function and a second provider make activation fail with the reason', async () => {
    const a = await host().activate(ext(['t.fn']), { slots: [] })
    const b = await host().activate(ext(['zzz']), { slots: [decl('zzz')] })
    const c = await host().activate(ext(['t.fn'], 42), { slots: [decl('t.fn')] })

    expect(a.status).toBe('failed')
    expect(a.detail).toContain("slot 't.fn' is not declared under slots: of fake/ext in plugin.yaml")
    expect(b.detail).toContain("unknown slot 'zzz' (this TUI defines: t.c, t.fn, t.other)")
    expect(c.detail).toContain("slot 't.fn' (fn) needs a function")

    const h = host()

    await h.activate(ext(['t.fn']), { name: 'one', slots: [decl('t.fn')] })

    const second = await h.activate(ext(['t.fn']), { name: 'two', slots: [decl('t.fn')] })

    expect(second.status).toBe('failed')
    expect(second.detail).toContain("slot 't.fn' is already provided by fake/one")
  })

  it('on_drift apply (the default): a drifted slot is mounted, listed as drifted, notified once, event drift-first-seen', async () => {
    const h = host({ upstream: { 't.fn': 'c'.repeat(64) } })

    await h.activate(ext(['t.fn']), { slots: [decl('t.fn')] })

    expect(h.runtime.slotFor('t.fn')?.extId).toBe('fake/ext')
    expect(h.runtime.slotRows()).toMatchObject([
      { drifted: true, id: 't.fn', status: 'active', upstreamCommit: '0123456789abcdef' }
    ])
    expect(
      h.logs.some(line => line.startsWith('tui slot t.fn active but drifted: upstream ui-tui/src/x.ts changed'))
    ).toBe(true)
    expect(h.notices.filter(n => n.text.includes('the copy runs anyway'))).toHaveLength(1)
    expect(h.slotEvents.map(e => e.kind)).toEqual(['drift-first-seen'])
    h.runtime.slotFailed('t.fn', 'boom') // the runtime net is unchanged: failures are counted like any slot
    expect(h.runtime.slotRows()[0]?.failures).toBe(1)
  })

  it('on_drift skip (the old behaviour): a drifted slot is not mounted, is listed with the reason and notified once', async () => {
    const h = host({ upstream: { 't.fn': 'c'.repeat(64) } })

    await h.activate(ext(['t.fn']), { slots: [decl('t.fn', { onDrift: 'skip' })] })

    expect(h.runtime.slotFor('t.fn')).toBeUndefined()
    expect(h.runtime.slotRows()).toMatchObject([
      { id: 't.fn', status: 'skipped-drift', upstreamCommit: '0123456789abcdef' }
    ])
    expect(h.notices.filter(n => n.text.includes('skipped-drift'))).toHaveLength(1)
    expect(h.slotEvents.map(e => e.kind)).toEqual(['drift-first-seen'])
  })

  it('a declared slot the extension never registered is an error row, not a silent gap', async () => {
    const h = host()

    await h.activate(ext(['t.fn']), { slots: [decl('t.fn'), decl('t.c')] })

    expect(h.runtime.slotRows().map(r => [r.id, r.status])).toEqual([
      ['t.fn', 'active'],
      ['t.c', 'skipped-error']
    ])
    expect(h.runtime.slotRows()[1]?.detail).toContain('did not register it')
    expect(h.slotEvents.map(e => e.kind)).toEqual(['applied', 'error'])
  })
})

describe('slot failures', () => {
  const setup = async (over: Record<string, unknown> = {}) => {
    const h = host(over)

    await h.activate(ext(['t.fn']), { slots: [decl('t.fn')] })

    return h
  }

  it('counts failures, warns on the first, switches the slot off at the third and keeps the extension active', async () => {
    const h = await setup()

    h.runtime.slotFailed('t.fn', 'boom 1')
    expect(h.runtime.slotFor('t.fn')).toBeDefined()
    expect(h.notices.filter(n => n.text.includes('slot t.fn failed: boom 1'))).toHaveLength(1)

    h.runtime.slotFailed('t.fn', 'boom 2')
    h.runtime.slotFailed('t.fn', 'boom 3')

    expect(h.runtime.slotFor('t.fn')).toBeUndefined()
    expect(h.runtime.slotRows()[0]).toMatchObject({ failures: 3, status: 'disabled-after-errors' })
    expect(h.notices.some(n => n.text.includes('slot t.fn switched off'))).toBe(true)
    expect(h.slotEvents.map(e => e.kind)).toEqual(['applied', 'error', 'auto-disabled'])
    expect(h.runtime.get('fake/ext')?.status).toBe('active')
  })

  it('immediate switches off at once (hook slots) and the switch-off is persisted: a new runtime refuses the slot', async () => {
    const store = memoryStore()
    const make = () => createRuntime({ notify: () => {}, slotDefs: DEFS, store, upstream: { 't.fn': HASH } })
    const api = (rt: ReturnType<typeof make>) => (e: any) =>
      ({ slots: { register: (id: string, impl: unknown) => rt.registerSlot(e, id, impl) } }) as never
    const first = make()
    const entry = first.track({ ...baseListing(), slots: [decl('t.fn')] }, 'loading', '')

    await first.activate(entry, ext(['t.fn']) as never, api(first))
    first.slotFailed('t.fn', 'hook blew up', { immediate: true })

    expect(store.read()['slot:t.fn']).toContain('hook blew up')

    const second = make()
    const again = second.track({ ...baseListing(), slots: [decl('t.fn')] }, 'loading', '')

    await second.activate(again, ext(['t.fn']) as never, api(second))

    expect(second.slotFor('t.fn')).toBeUndefined()
    expect(second.slotRows()[0]).toMatchObject({ status: 'disabled-after-errors' })

    second.enable('t.fn')
    expect(store.read()['slot:t.fn']).toBeUndefined()
  })

  it('disableSlot persists and unmounts; a failure of an unmounted slot is ignored', async () => {
    const h = await setup()

    expect(h.runtime.disableSlot('t.fn', 'by hand')).toContain('slot t.fn disabled')
    expect(h.runtime.slotFor('t.fn')).toBeUndefined()
    h.runtime.slotFailed('t.fn', 'late')
    expect(h.runtime.slotRows()[0]?.failures).toBe(0)
  })
})

describe('slot rows follow the extension', () => {
  it('an ungranted or disabled extension still lists its slots, with the matching status', () => {
    const h = host()
    const a = h.runtime.track(
      { ...baseListing(), id: 'p/a', name: 'a', slots: [decl('t.fn')] },
      'not-granted',
      'not granted: set it'
    )
    const b = h.runtime.track({ ...baseListing(), id: 'p/b', name: 'b', slots: [decl('t.c')] }, 'loading', '')

    expect(h.runtime.slotRows().map(r => [r.id, r.status])).toEqual([
      ['t.fn', 'skipped-no-grant'],
      ['t.c', 'pending']
    ])
    h.runtime.setStatus(b, 'disabled', 'x')
    expect(h.runtime.slotRows()[1]?.status).toBe('disabled-after-errors')
    h.runtime.setStatus(a, 'failed', 'x')
    expect(h.runtime.slotRows()[0]?.status).toBe('skipped-error')
  })

  it('the epoch moves and subscribers are told when a slot is mounted or switched off', async () => {
    const h = host()
    let told = 0
    const stop = h.runtime.subscribeSlots(() => void (told += 1))
    const before = h.runtime.slotEpoch()

    await h.activate(ext(['t.fn']), { slots: [decl('t.fn')] })

    const mounted = h.runtime.slotEpoch()

    h.runtime.slotFailed('t.fn', 'x', { immediate: true })

    expect(mounted).toBeGreaterThan(before)
    expect(h.runtime.slotEpoch()).toBeGreaterThan(mounted)
    expect(told).toBeGreaterThan(2)
    stop()
  })
})
