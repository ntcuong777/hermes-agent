import { describe, expect, it, vi } from 'vitest'

import { applyStashSelection, openStashes, selectStash } from '../app/stashPicker.js'
import type { WidgetInput } from '../sdk/types.js'

const input = (ch: string, key: Partial<WidgetInput['key']> = {}): WidgetInput => ({ ch, key }) as WidgetInput

const state = {
  entries: ['first', 'second'],
  maximum: 9,
  selected: 1,
  chosen: null,
  dropped: null,
  apply: vi.fn(),
  drop: vi.fn()
}

describe('stash picker', () => {
  it('selects numbered entries and cancels without applying', () => {
    expect(selectStash(state, input('1'))?.chosen).toBe(0)
    expect(selectStash(state, input('', { return: true }))?.chosen).toBe(1)
    expect(selectStash(state, input('', { upArrow: true }))?.selected).toBe(0)
    expect(selectStash(state, input('', { escape: true }))).toBeNull()
    expect(state.apply).not.toHaveBeenCalled()
  })

  it('acknowledges the exact selected draft only after composer accepts it', async () => {
    const acknowledge = vi.fn(async () => {})
    const accept = vi.fn(() => true)
    await applyStashSelection({ index: 0, text: 'first\nline', accept, acknowledge })
    expect(accept).toHaveBeenCalledWith('first\nline')
    expect(acknowledge).toHaveBeenCalledWith('{"index":0,"text":"first\\nline"}')
    expect(accept.mock.invocationCallOrder[0]).toBeLessThan(acknowledge.mock.invocationCallOrder[0]!)
  })

  it('keeps stored draft when composer changed during fetch or selection', async () => {
    const acknowledge = vi.fn(async () => {})
    await applyStashSelection({ index: 1, text: 'second', accept: () => false, acknowledge })
    expect(acknowledge).not.toHaveBeenCalled()
  })

  it('marks the selected entry for deletion on x or d, and nothing else', () => {
    expect(selectStash(state, input('x'))?.dropped).toBe(1)
    expect(selectStash(state, input('d', {}))?.dropped).toBe(1)
    expect(selectStash(state, input('x'))?.chosen).toBeNull()
    expect(selectStash(state, input('2'))?.dropped).toBeNull()
  })
})

// Pi's stash extension: one entry restores at once, several open the list.
describe('opening the stash', () => {
  const snapshot = (...entries: string[]) => JSON.stringify({ entries, count: entries.length, max: 9 })

  it('restores a lone entry without a picker', () => {
    const apply = vi.fn()
    expect(openStashes(snapshot('only one'), apply, vi.fn())).toBe('applied')
    expect(apply).toHaveBeenCalledExactlyOnceWith(0, 'only one')
  })

  it('reports an empty stash and applies nothing', () => {
    const apply = vi.fn()
    expect(openStashes(snapshot(), apply, vi.fn())).toBe('empty')
    expect(apply).not.toHaveBeenCalled()
  })

  it('opens the picker for several entries and applies nothing yet', () => {
    const apply = vi.fn()
    expect(openStashes(snapshot('a', 'b'), apply, vi.fn())).toBe('picker')
    expect(apply).not.toHaveBeenCalled()
  })
})
