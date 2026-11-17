import { Text } from '@hermes/ink'
import React, { useEffect } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SlotDeclaration, SlotDef } from '../../sdk/ext/contract.js'
import { renderOutput, stripAnsi } from '../../sdk/ext/frames.js'
import { slotComponent, slotFn, slotHook } from '../../sdk/ext/slotReact.js'
import { createFakeHost } from '../../sdk/ext/testing.js'

const HASH = 'a'.repeat(64)

const def = (kind: SlotDef['kind']): SlotDef => ({
  file: 'ui-tui/src/x.ts',
  host: [],
  kind,
  props: 1,
  summary: 'x',
  symbols: ['x']
})

const DEFS = { 't.c': def('component'), 't.fn': def('fn'), 't.hook': def('hook') }
const decl = (id: string): SlotDeclaration => ({
  expectUpstream: HASH,
  id,
  props: 1,
  reason: 'r',
  upstreamCommit: 'abcdef0123'
})

const mount = async (id: keyof typeof DEFS, impl: unknown) => {
  const h = createFakeHost({ slotDefs: DEFS, upstream: { [id]: HASH } })

  await h.activate({ activate: (api: any) => api.slots.register(id, impl), sdkVersion: 1 }, { slots: [decl(id)] })

  return h
}

beforeEach(() => void vi.spyOn(process.stdout, 'write').mockImplementation(() => true))

describe('slotFn', () => {
  it('is the stock function with nothing mounted', () => {
    const h = createFakeHost({ slotDefs: DEFS })

    expect(slotFn('t.fn', (a: number) => a + 1, h.host)(1)).toBe(2)
  })

  it('uses the extension function, and falls back to stock for the call when it throws', async () => {
    const h = await mount('t.fn', (a: number) => {
      if (a < 0) {
        throw new Error('negative')
      }

      return a * 10
    })

    const f = slotFn('t.fn', (a: number) => a + 1, h.host)

    expect(f(2)).toBe(20)
    expect(f(-1)).toBe(0)
    expect(h.runtime.slotRows()[0]).toMatchObject({ failures: 1, status: 'active' })
  })

  it('three throws switch the slot off; later calls are stock without calling the extension', async () => {
    const impl = vi.fn(() => {
      throw new Error('always')
    })

    const h = await mount('t.fn', impl)
    const f = slotFn('t.fn', () => 'stock', h.host)

    expect([f(), f(), f(), f()]).toEqual(['stock', 'stock', 'stock', 'stock'])
    expect(impl).toHaveBeenCalledTimes(3)
    expect(h.runtime.slotRows()[0]?.status).toBe('disabled-after-errors')
  })

  it('a rejected answer (check) falls back and counts; a slow call counts but its answer is kept', async () => {
    const h = await mount('t.fn', () => 'bad')

    expect(
      slotFn(
        't.fn',
        () => 'stock',
        h.host,
        v => v !== 'bad'
      )()
    ).toBe('stock')
    expect(h.runtime.slotRows()[0]?.failures).toBe(1)

    const slow = await mount('t.fn', () => 'slow answer')
    const clock = vi.spyOn(performance, 'now').mockReturnValueOnce(0).mockReturnValueOnce(900)

    expect(slotFn('t.fn', () => 'stock', slow.host)()).toBe('slow answer')
    expect(slow.runtime.slotRows()[0]?.failures).toBe(1)
    expect(slow.runtime.get('fake/ext')?.errors.at(-1)).toContain('blocked the UI for 900ms')
    clock.mockRestore()
  })
})

describe('slotComponent', () => {
  const Stock = ({ n }: { n: number }) => <Text>stock {n}</Text>

  it('renders stock with nothing mounted and the extension component when mounted', async () => {
    const none = createFakeHost({ slotDefs: DEFS })
    const Plain = slotComponent('t.c', Stock, none.host)

    expect(stripAnsi(await renderOutput(<Plain n={1} />))).toContain('stock 1')

    const h = await mount('t.c', ({ n }: { n: number }) => <Text>ext {n}</Text>)
    const Slot = slotComponent('t.c', Stock, h.host)

    expect(stripAnsi(await renderOutput(<Slot n={2} />))).toContain('ext 2')
  })

  it('a render that throws shows the stock component on the same props at once and counts the failure', async () => {
    const h = await mount('t.c', () => {
      throw new Error('render boom')
    })

    const Slot = slotComponent('t.c', Stock, h.host)
    const text = stripAnsi(await renderOutput(<Slot n={3} />))

    expect(text).toContain('stock 3')
    expect(h.runtime.slotRows()[0]).toMatchObject({ failures: 1, status: 'active' })
    expect(h.runtime.get('fake/ext')?.errors.at(-1)).toContain('slot t.c: render: render boom')
  })

  it('after the third failure the slot is off and later mounts are stock', async () => {
    const h = await mount('t.c', () => {
      throw new Error('again')
    })

    const Slot = slotComponent('t.c', Stock, h.host)

    for (let i = 0; i < 3; i += 1) {
      await renderOutput(<Slot n={i} />)
    }

    expect(h.runtime.slotRows()[0]?.status).toBe('disabled-after-errors')
    expect(stripAnsi(await renderOutput(<Slot n={9} />))).toContain('stock 9')
  })

  it('re-renders when the slot table changes (an extension mounted after the first frame)', async () => {
    const h = createFakeHost({ slotDefs: DEFS, upstream: { 't.c': HASH } })
    const Slot = slotComponent('t.c', Stock, h.host)
    const first = await renderOutput(<Slot n={1} />)

    await h.activate(
      { activate: (api: any) => api.slots.register('t.c', () => <Text>late</Text>), sdkVersion: 1 },
      { slots: [decl('t.c')] }
    )

    expect(stripAnsi(first)).toContain('stock 1')
    expect(stripAnsi(await renderOutput(<Slot n={1} />))).toContain('late')
  })
})

describe('slotHook', () => {
  const Probe = ({ useIt }: { useIt: () => string }) => {
    const value = useIt()

    useEffect(() => {}, [])

    return <Text>{value}</Text>
  }

  it('is the stock hook with nothing mounted and the extension hook once mounted (chosen per mount)', async () => {
    const none = createFakeHost({ slotDefs: DEFS })

    const stockHook = () => {
      const [v] = React.useState('stock hook')

      return v
    }

    const A = slotHook('t.hook', stockHook, none.host)

    expect(stripAnsi(await renderOutput(<Probe useIt={A} />))).toContain('stock hook')

    const h = await mount('t.hook', () => {
      const [v] = React.useState('ext hook')

      return v
    })

    const B = slotHook('t.hook', stockHook, h.host)

    expect(stripAnsi(await renderOutput(<Probe useIt={B} />))).toContain('ext hook')
  })

  it("a hook that throws while its component first mounts is switched off at once (persisted); React's retry renders stock", async () => {
    const h = await mount('t.hook', () => {
      throw new Error('hook boom')
    })

    const B = slotHook('t.hook', () => 'stock', h.host)

    expect(stripAnsi(await renderOutput(<Probe useIt={B} />))).toContain('stock')
    expect(h.runtime.slotRows()[0]).toMatchObject({ status: 'disabled-after-errors' })
    expect(h.runtime.slotRows()[0]?.detail).toContain('hook boom')
  })
})
