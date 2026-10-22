import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $stashCounts, refreshStashCounts } from '../app/stashCounts.js'

const answer = (output: string) => vi.fn(async () => ({ output }))

describe('stash counts', () => {
  beforeEach(() => $stashCounts.set({ session: 0, global: 0 }))

  it('reads both counts from the server', async () => {
    await refreshStashCounts(answer('{"session":2,"global":1,"max":9}'), 'sid-1')

    expect($stashCounts.get()).toEqual({ session: 2, global: 1 })
  })

  it('asks for the counts of the session it was given', async () => {
    const rpc = answer('{"session":0,"global":0,"max":9}')
    await refreshStashCounts(rpc, 'sid-1')

    expect(rpc).toHaveBeenCalledExactlyOnceWith('slash.exec', { command: 'stash counts', session_id: 'sid-1' })
  })

  it('keeps the last counts when the answer is not the counts (plugin off, error text)', async () => {
    $stashCounts.set({ session: 4, global: 2 })
    await refreshStashCounts(answer('unknown command'), 'sid-1')

    expect($stashCounts.get()).toEqual({ session: 4, global: 2 })
  })

  it('keeps the last counts when the call fails', async () => {
    $stashCounts.set({ session: 4, global: 2 })
    await refreshStashCounts(vi.fn().mockRejectedValue(new Error('down')), 'sid-1')

    expect($stashCounts.get()).toEqual({ session: 4, global: 2 })
  })

  it('does nothing without a session', async () => {
    const rpc = answer('{"session":9,"global":9,"max":9}')
    await refreshStashCounts(rpc, null)

    expect(rpc).not.toHaveBeenCalled()
  })
})
