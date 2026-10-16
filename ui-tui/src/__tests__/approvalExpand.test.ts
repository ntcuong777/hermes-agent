import { describe, expect, it } from 'vitest'

import { approvalAction, approvalPreview } from '../components/prompts.js'

const lines = Array.from({ length: 25 }, (_, i) => `line ${i + 1}`)

describe('approval panel expand (B16)', () => {
  it('shows the first 10 lines and reports the rest when collapsed', () => {
    const { shown, overflow } = approvalPreview(lines, false)

    expect(shown).toHaveLength(10)
    expect(shown[9]).toBe('line 10')
    expect(overflow).toBe(15)
  })

  it('shows every line when expanded, so nothing is cut', () => {
    const { shown, overflow } = approvalPreview(lines, true)

    expect(shown).toEqual(lines)
    expect(overflow).toBe(0)
  })

  it('a short command has nothing to expand', () => {
    expect(approvalPreview(['ls -la'], false)).toEqual({ shown: ['ls -la'], overflow: 0 })
  })

  it('v toggles the full text and is not a choice key', () => {
    expect(approvalAction('v', {}, 0)).toEqual({ kind: 'toggle' })
  })
})
