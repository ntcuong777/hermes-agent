import { describe, expect, it } from 'vitest'

import { approvalAction, approvalPreview, approvalSplit } from '../components/prompts.js'

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

describe('approval panel shows what the rule is about (B16, C168)', () => {
  it('the first line of the description is the title; the rest is body text that v can expand', () => {
    const description = 'Possible secret file. NEVER read secrets.\ncat .env\nand a second line'

    expect(approvalSplit(description)).toEqual({ title: 'Possible secret file. NEVER read secrets.', detail: ['cat .env', 'and a second line'] })
  })

  it('a one-line description has no detail', () => {
    expect(approvalSplit('Recursive force delete (rm -rf).')).toEqual({ title: 'Recursive force delete (rm -rf).', detail: [] })
  })

  it('a long detail is cut to the preview when collapsed and complete when expanded', () => {
    const detail = Array.from({ length: 30 }, (_, i) => `part ${i + 1}`)

    expect(approvalPreview(detail, false).overflow).toBe(20)
    expect(approvalPreview(detail, true).shown).toEqual(detail)
  })
})
