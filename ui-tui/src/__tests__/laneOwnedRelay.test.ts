import { beforeEach, expect, it, vi } from 'vitest'

import { createSlashHandler } from '../app/createSlashHandler.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'

// A session the Discord session mirror bound to a thread: the owner refuses a transcript write sent
// over its own RPC as `lane_owned` (gateway/session_lane.py), and the TUI re-sends the command as
// typed through `slash.exec`, which relays it to the thread. The user sees the relay's one-line ack,
// never an error.

const flush = () => new Promise(resolve => setImmediate(resolve))

const laneOwned = () =>
  Object.assign(new Error('lane_owned'), {
    code: 4001,
    data: { reason: 'lane_owned', detail: 'its bound thread writes this conversation' }
  })

function harness(refuse: (method: string) => boolean) {
  const request = vi.fn(async (method: string, params: any) => {
    if (method === 'slash.exec') {
      return { output: `/${params.command} sent to the session's bound thread; its output posts there.` }
    }

    if (method === 'session.resume') {
      return { execution_generation: 3, messages: [{ content: 'hi', role: 'user', row_id: 7 }], revision: 4 }
    }

    if (refuse(method)) {
      throw laneOwned()
    }

    return {}
  })

  const sys = vi.fn()

  const ctx = {
    slashFlightRef: { current: 0 },
    gateway: { gw: { isCanonical: true, request }, rpc: request },
    transcript: {
      page: vi.fn(),
      panel: vi.fn(),
      send: vi.fn(),
      sys,
      setHistoryItems: vi.fn(),
      trimLastExchange: (items: unknown[]) => items
    },
    local: { getHistoryItems: () => [], getLastUserMsg: () => '', maybeWarn: vi.fn() },
    session: { resumeById: vi.fn() },
    composer: { enqueue: vi.fn(), setInput: vi.fn() }
  }

  return { request, slash: createSlashHandler(ctx as any), sys }
}

beforeEach(() => {
  resetUiState()
  patchUiState({
    sid: 'bound',
    info: { model: 'm', skills: {}, tools: {}, execution_epoch: '1', execution_generation: 3 } as any
  })
})

it.each([
  ['/bg summarize the repo', 'prompt.background', 'bg summarize the repo'],
  ['/btw what was that error', 'prompt.btw', 'btw what was that error'],
  ['/compress keep the plan', 'session.mutate', 'compress keep the plan'],
  ['/undo', 'session.mutate', 'undo'],
  ['/rollback 2', 'rollback.restore', 'rollback 2']
])('%s on a lane-owned session is relayed to the bound thread as typed', async (typed, method, relayed) => {
  const { request, slash, sys } = harness(m => m === method || m === 'rollback.list')

  slash(typed)

  for (let i = 0; i < 5; i++) {
    await flush()
  }

  expect(request).toHaveBeenCalledWith('slash.exec', { command: relayed, session_id: 'bound' })
  const shown = sys.mock.calls.flat().join('\n')
  expect(shown).toContain('sent to the session')
  expect(shown).not.toMatch(/^error:|lane_owned|permission/im)
})

it('a refusal that is not lane_owned is still shown, never relayed', async () => {
  const request = vi.fn(async (method: string) => {
    if (method === 'prompt.btw') {
      throw Object.assign(new Error('permission_denied'), { code: 4001, data: { reason: 'permission_denied' } })
    }

    return {}
  })

  const sys = vi.fn()

  const slash = createSlashHandler({
    slashFlightRef: { current: 0 },
    gateway: { gw: { isCanonical: true, request }, rpc: request },
    transcript: { page: vi.fn(), panel: vi.fn(), send: vi.fn(), sys },
    local: { getHistoryItems: () => [], getLastUserMsg: () => '', maybeWarn: vi.fn() },
    session: { resumeById: vi.fn() },
    composer: { enqueue: vi.fn() }
  } as any)

  slash('/btw why')
  await flush()
  await flush()

  expect(request).not.toHaveBeenCalledWith('slash.exec', expect.anything())
  expect(sys.mock.calls.flat().join('\n')).toMatch(/permission/i)
})
