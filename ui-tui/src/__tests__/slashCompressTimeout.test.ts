import { beforeEach, expect, it, vi } from 'vitest'

import { createSlashHandler } from '../app/createSlashHandler.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import { SESSION_COMPRESS_TIMEOUT_MS } from '../lib/rpc.js'

// /compact answers only after its minutes-long summarization: on a resumed
// 300k-token session it ran 121s against the 120s default request deadline, so
// the TUI printed a false timeout while the compression went on to commit.
// Both compress paths must send the Desktop-parity budget (above the gateway's
// 630s compute-host wait cap); every other command keeps the default.

const flush = () => new Promise(resolve => setImmediate(resolve))

beforeEach(() => {
  resetUiState()
})

it('the legacy /compress rpc carries the long compress budget', async () => {
  patchUiState({ sid: 'legacy-1' })
  const rpc = vi.fn(async () => null)
  const ctx = {
    slashFlightRef: { current: 0 },
    gateway: { gw: { isCanonical: false, request: vi.fn(async () => ({})) }, rpc },
    transcript: { sys: vi.fn(), setHistoryItems: vi.fn() },
    local: { maybeWarn: vi.fn() },
    session: { resumeById: vi.fn() },
    composer: { enqueue: vi.fn() }
  }
  const slash = createSlashHandler(ctx as any)

  slash('/compress')
  await flush()

  const compressCall = rpc.mock.calls.find(([method]) => method === 'session.compress')
  expect(compressCall).toBeDefined()
  expect(compressCall![1]).toEqual({ session_id: 'legacy-1' })
  expect(compressCall![2]).toEqual({ timeoutMs: SESSION_COMPRESS_TIMEOUT_MS })
  // The compaction indicator is armed for the wait and dropped after the reply.
  expect(getUiState().manualCompacting).toBe(false)
})

it('the canonical /compress mutation carries the budget; model and branch keep the default', async () => {
  patchUiState({ sid: 'owner' })
  const request = vi.fn(async (method: string, _params: any) => {
    if (method === 'session.resume') {
      return { session_id: 'owner', revision: 8, execution_generation: 4, messages: [] }
    }

    return { execution_generation: 5 }
  })
  const ctx = {
    slashFlightRef: { current: 0 },
    gateway: { gw: { isCanonical: true, request }, rpc: request },
    transcript: { sys: vi.fn(), setHistoryItems: vi.fn() },
    local: { maybeWarn: vi.fn() },
    session: { resumeById: vi.fn() },
    composer: { enqueue: vi.fn() }
  }
  const slash = createSlashHandler(ctx as any)

  slash('/model picked --tui-session')
  await flush()
  slash('/branch Child')
  await flush()
  slash('/compress')
  await flush()

  const mutations = request.mock.calls.filter(([method]) => method === 'session.mutate')
  expect(mutations).toHaveLength(3)
  expect(mutations[0]![1]).toMatchObject({ operation: 'model' })
  expect(mutations[0]![2]).toBeUndefined()
  expect(mutations[1]![1]).toMatchObject({ operation: 'branch' })
  expect(mutations[1]![2]).toBeUndefined()
  expect(mutations[2]![1]).toMatchObject({ operation: 'compress' })
  expect(mutations[2]![2]).toBe(SESSION_COMPRESS_TIMEOUT_MS)
})
