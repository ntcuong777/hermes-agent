import { describe, expect, it } from 'vitest'

import { costLabel, quotaLabel, quotaSeverity, quotaWindowName, stashLabel } from '../lib/statusUsage.js'

describe('costLabel', () => {
  it('shows the session total in dollars', () => {
    expect(costLabel({ cost_status: 'actual', cost_usd: 1.234 })).toBe('$1.23')
  })

  it('marks an estimate with ~ because it is a lower bound', () => {
    expect(costLabel({ cost_status: 'estimated', cost_usd: 0.4231 })).toBe('~$0.42')
  })

  it('never rounds a real spend down to $0.00', () => {
    expect(costLabel({ cost_status: 'estimated', cost_usd: 0.002 })).toBe('~<$0.01')
  })

  it('says incl. for a subscription that bills nothing', () => {
    expect(costLabel({ cost_status: 'included' })).toBe('incl.')
  })

  it('says n/a, not nothing, when calls were made and none had a price (B17)', () => {
    expect(costLabel({ cost_status: 'unpriced' })).toBe('n/a')
  })

  it('says nothing without data', () => {
    expect(costLabel({})).toBe('')
    expect(costLabel({ cost_status: 'unknown', cost_usd: 0 })).toBe('')
    expect(costLabel({ cost_usd: Number.NaN })).toBe('')
  })
})

describe('quotaWindowName', () => {
  it('reads Codex and Anthropic labels the same way', () => {
    expect(quotaWindowName('Session')).toBe('5h')
    expect(quotaWindowName('Current session')).toBe('5h')
    expect(quotaWindowName('Weekly')).toBe('wk')
    expect(quotaWindowName('Current week')).toBe('wk')
  })

  it('keeps the first word of anything else, short', () => {
    expect(quotaWindowName('API key quota')).toBe('api')
    expect(quotaWindowName('Subscription')).toBe('subscr')
    expect(quotaWindowName('')).toBe('?')
  })
})

describe('quotaLabel', () => {
  it('joins the windows as percent used', () => {
    expect(
      quotaLabel([
        { label: 'Session', used_percent: 12.4 },
        { label: 'Weekly', used_percent: 60 }
      ])
    ).toBe('5h 12% wk 60%')
  })

  it('skips windows with no percent and clamps the rest', () => {
    expect(quotaLabel([{ label: 'Weekly' }, { label: 'Session', used_percent: 140 }])).toBe('5h 100%')
  })

  it('is empty without windows', () => {
    expect(quotaLabel(undefined)).toBe('')
    expect(quotaLabel(null)).toBe('')
    expect(quotaLabel([])).toBe('')
  })
})

describe('quotaSeverity', () => {
  it('follows the fullest window', () => {
    expect(quotaSeverity([{ label: 'Session', used_percent: 10 }])).toBe('ok')
    expect(
      quotaSeverity([
        { label: 'Session', used_percent: 10 },
        { label: 'Weekly', used_percent: 70 }
      ])
    ).toBe('warn')
    expect(quotaSeverity([{ label: 'Weekly', used_percent: 90 }])).toBe('bad')
  })

  it('is ok without data', () => {
    expect(quotaSeverity(undefined)).toBe('ok')
  })
})

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
