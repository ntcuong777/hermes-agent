import { describe, expect, it } from 'vitest'

import { approvalAction, approvalScrollHints } from '../components/prompts.js'

// B27: the expanded approval panel told the person to press PgUp/PgDn. Many terminals and
// laptops have no such keys; Ctrl+U / Ctrl+D already scrolled, they were just not shown.
describe('approval panel scroll hints', () => {
  it('name keys every keyboard has, not only PgUp/PgDn', () => {
    const h = approvalScrollHints()

    expect(h.above).toContain('Ctrl+U')
    expect(h.below).toContain('Ctrl+D')
    expect(h.keys).toContain('Ctrl+U')
    expect(h.keys).toContain('Ctrl+D')
    expect(`${h.above}${h.below}${h.keys}`).not.toMatch(/PgUp|PgDn/)
  })

  it('every key it names does scroll the panel', () => {
    expect(approvalAction('d', { ctrl: true }, 0)).toEqual({ kind: 'scroll', dir: 1 })
    expect(approvalAction('u', { ctrl: true }, 0)).toEqual({ kind: 'scroll', dir: -1 })
  })
})
