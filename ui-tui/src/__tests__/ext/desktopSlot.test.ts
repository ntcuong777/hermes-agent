import { afterEach, describe, expect, it, vi } from 'vitest'

import { forkSlot } from '../../../../apps/desktop/src/fork/slot.js'

const STOCK = 'stock'
const FORK = 'fork'
const setHashes = (hashes?: Record<string, string>) =>
  Object.defineProperty(globalThis, '__HERMES_SLOT_HASHES__', { configurable: true, value: hashes })

describe('the Desktop build-time slot (spec 09)', () => {
  afterEach(() => {
    setHashes(undefined)
    vi.restoreAllMocks()
  })

  it('a matching hash builds the fork copy without a warning', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    setHashes({ card: 'a'.repeat(64) })
    expect(forkSlot('card', STOCK, FORK, 'a'.repeat(64))).toBe(FORK)
    expect(warn).not.toHaveBeenCalled()
  })

  it('on_drift apply (the default): a changed hash still builds the fork copy and warns once, naming both hashes', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    setHashes({ card: 'b'.repeat(64) })
    expect(forkSlot('card', STOCK, FORK, 'a'.repeat(64))).toBe(FORK)
    expect(warn).toHaveBeenCalledTimes(1)
    expect(String(warn.mock.calls[0]?.[0])).toContain('bbbbbbbbbb != recorded aaaaaaaaaa')
  })

  it('an unknown on_drift argument is apply (like the env var), with the same warning; skip is only the exact word', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    setHashes({ card: 'b'.repeat(64) })
    expect(forkSlot('card', STOCK, FORK, 'a'.repeat(64), 'sometimes' as never)).toBe(FORK)
    expect(warn).toHaveBeenCalledTimes(1)
    expect(forkSlot('card', STOCK, FORK, 'a'.repeat(64), null as never)).toBe(FORK)
  })

  it('on_drift skip: upstream builds (the old behaviour); a missing build hash always builds upstream', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    setHashes({ card: 'b'.repeat(64) })
    expect(forkSlot('card', STOCK, FORK, 'a'.repeat(64), 'skip')).toBe(STOCK)
    setHashes({})
    expect(forkSlot('card', STOCK, FORK, 'a'.repeat(64))).toBe(STOCK)
  })
})
