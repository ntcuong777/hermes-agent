import { describe, expect, it } from 'vitest'

import { stashLabel } from '../lib/statusUsage.js'

// Pi's stash extension shows `stash 📋N`, and `📋N+M` once the shared stash is in use.
describe('stashLabel', () => {
  it('is empty when nothing is stashed', () => {
    expect(stashLabel({ session: 0, global: 0 })).toBe('')
  })

  it('shows the session count alone while the profile stash is empty', () => {
    expect(stashLabel({ session: 3, global: 0 })).toBe('📋3')
  })

  it('adds the profile count after a plus once it is in use, even with an empty session stash', () => {
    expect(stashLabel({ session: 2, global: 1 })).toBe('📋2+1')
    expect(stashLabel({ session: 0, global: 4 })).toBe('📋0+4')
  })
})
