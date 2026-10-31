import { describe, expect, it } from 'vitest'

import { expandPasteTokens } from '../lib/text.js'

describe('expandPasteTokens', () => {
  const label = '[[ big head.. 40 lines .. tail ]]'
  const full = `big head\n${'line\n'.repeat(38)}tail`

  it('replaces a paste label with the full collapsed text, wherever it sits', () => {
    const tokens = [{ kind: 'paste', label, text: full }]
    expect(expandPasteTokens(`before ${label} after`, tokens)).toBe(`before ${full} after`)
  })

  it('leaves text alone for non-paste tokens, tokens without text, and unknown labels', () => {
    const text = `kept ${label} kept`
    expect(expandPasteTokens(text, [{ kind: 'image', label }])).toBe(text)
    expect(expandPasteTokens(text, [{ kind: 'paste', label }])).toBe(text)
    expect(expandPasteTokens(text, [{ kind: 'paste', label: '[[ other ]]', text: 'x' }])).toBe(text)
  })

  it('expands several tokens in one pass', () => {
    const a = { kind: 'paste' as const, label: '[[ a ]]', text: 'AAA' }
    const b = { kind: 'paste' as const, label: '[[ b ]]', text: 'BBB' }
    expect(expandPasteTokens('x [[ a ]] y [[ b ]] z', [a, b])).toBe('x AAA y BBB z')
  })
})
