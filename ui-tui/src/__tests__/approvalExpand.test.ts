import { describe, expect, it } from 'vitest'

import { approvalAction, approvalPreview, approvalSplit, approvalWindow } from '../components/prompts.js'

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

describe('approval panel scrolls the full text (B16)', () => {
  const many = Array.from({ length: 50 }, (_, i) => `row ${i + 1}`)

  it('PageDown / PageUp / Ctrl+D / Ctrl+U scroll and are not choices', () => {
    expect(approvalAction('', { pageDown: true }, 0)).toEqual({ kind: 'scroll', dir: 1 })
    expect(approvalAction('', { pageUp: true }, 0)).toEqual({ kind: 'scroll', dir: -1 })
    expect(approvalAction('d', { ctrl: true }, 0)).toEqual({ kind: 'scroll', dir: 1 })
    expect(approvalAction('u', { ctrl: true }, 0)).toEqual({ kind: 'scroll', dir: -1 })
  })

  it('a window shows `size` lines from the offset and says how many are above and below', () => {
    expect(approvalWindow(many, 10, 8)).toEqual({ above: 10, below: 32, offset: 10, shown: many.slice(10, 18) })
  })

  it('the offset is clamped so the last page stays full and the first never goes negative', () => {
    expect(approvalWindow(many, 999, 8)).toMatchObject({ offset: 42, below: 0, above: 42 })
    expect(approvalWindow(many, -5, 8)).toMatchObject({ offset: 0, above: 0 })
  })

  it('text that fits is shown whole with nothing to scroll', () => {
    expect(approvalWindow(['a', 'b'], 3, 8)).toEqual({ above: 0, below: 0, offset: 0, shown: ['a', 'b'] })
  })
})
