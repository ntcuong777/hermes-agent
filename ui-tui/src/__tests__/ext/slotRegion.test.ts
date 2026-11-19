import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { normalize, regionHash, regionText, statements } from '../../sdk/ext/slotRegion.mjs'

interface Vector {
  error?: boolean
  name: string
  sha256?: string
  source: string
  symbols: string[]
}

const vectors = JSON.parse(
  readFileSync(new URL('../../sdk/ext/slotRegion.vectors.json', import.meta.url), 'utf8')
) as Vector[]

describe('slotRegion', () => {
  it.each(vectors)('vector: $name', v => {
    if (v.error) {
      expect(() => regionHash(v.source, v.symbols)).toThrow("symbol 'nope' not found")
    } else {
      expect(regionHash(v.source, v.symbols)).toBe(v.sha256)
    }
  })

  it('splits a file into top-level statements and keeps the doc comment with its declaration', () => {
    const found = statements('const a = 1\n\n/** doc\n * more */\nexport function b() {\n  return 1\n}\n')

    expect(found.map(s => s.head)).toEqual(['const a = 1', 'export function b() {'])
    expect(found[1]?.text).toBe('/** doc\n * more */\nexport function b() {\n  return 1\n}')
  })

  it('normalizes line endings and trailing blanks', () => {
    expect(normalize('a  \r\nb\t\r\n\r\n')).toBe('a\nb')
  })

  it('the region of two symbols is their statements in the order asked', () => {
    expect(regionText('const a = 1\nconst b = 2\n', ['b', 'a'])).toBe('const b = 2\nconst a = 1')
  })

  it('region of a real component moves with its own edits only', () => {
    const file = readFileSync(new URL('../../components/prompts.tsx', import.meta.url), 'utf8')
    const base = regionHash(file, ['ApprovalPrompt'])

    expect(regionHash(`${file}\nexport const unrelated = 1\n`, ['ApprovalPrompt'])).toBe(base)
    expect(regionHash(file.replace('Esc/Ctrl+C deny', 'Esc/Ctrl+C DENY'), ['ApprovalPrompt'])).not.toBe(base)
  })
})
