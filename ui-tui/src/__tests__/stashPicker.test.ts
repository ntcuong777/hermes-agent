import { describe, expect, it, vi } from 'vitest'

import { applyStashSelection, selectStash } from '../app/stashPicker.js'
import type { WidgetInput } from '../sdk/types.js'

const input = (ch: string, key: Partial<WidgetInput['key']> = {}): WidgetInput => ({ ch, key }) as WidgetInput

const state = {
  entries: ['first', 'second'],
  maximum: 9,
  selected: 1,
  chosen: null,
  apply: vi.fn()
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
})
