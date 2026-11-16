import { describe, expect, it, vi } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { patchUiState } from '../app/uiStore.js'

const ref = <T>(current: T) => ({ current })

const ctx = (sys = vi.fn()) =>
  ({
    composer: { dequeue: () => undefined, queueEditRef: ref<null | number>(null), sendQueued: vi.fn(), setInput: vi.fn() },
    gateway: { gw: { request: vi.fn(async () => null) }, rpc: vi.fn(async () => null) },
    session: { STARTUP_RESUME_ID: '', colsRef: ref(80), newSession: vi.fn(), resetSession: vi.fn(), resumeById: vi.fn(), setCatalog: vi.fn() },
    submission: { submitRef: { current: vi.fn() } },
    system: { bellOnComplete: false, sys },
    transcript: { appendMessage: vi.fn(), panel: vi.fn(), setHistoryItems: vi.fn() },
    voice: { setProcessing: vi.fn(), setRecording: vi.fn(), setVoiceEnabled: vi.fn() }
  }) as any

const emit = (sys: ReturnType<typeof vi.fn>, payload: unknown, sid = 'sess-1') =>
  createGatewayEventHandler(ctx(sys))({ payload, session_id: sid, type: 'command.output' } as any)

describe('command.output from a lane the session is mirrored onto', () => {
  it('prints the command and its answer into the transcript', () => {
    patchUiState({ sid: 'sess-1' })
    const sys = vi.fn()

    emit(sys, { command: '/goal clear', output: 'Goal cleared.' })

    expect(sys).toHaveBeenCalledWith('↳ /goal clear\nGoal cleared.')
  })

  it('prints a bare answer when the command line is unknown, and nothing for an empty one', () => {
    patchUiState({ sid: 'sess-1' })
    const sys = vi.fn()

    emit(sys, { output: '  plain  ' })
    emit(sys, { command: '/x', output: '   ' })
    emit(sys, { command: '/x' })
    emit(sys, undefined)

    expect(sys.mock.calls).toEqual([['plain']])
  })

  it('ignores another session’s output', () => {
    patchUiState({ sid: 'sess-1' })
    const sys = vi.fn()

    emit(sys, { command: '/goal clear', output: 'Goal cleared.' }, 'sess-2')

    expect(sys).not.toHaveBeenCalled()
  })
})

describe('session.info that lost its route', () => {
  const info = (payload: Record<string, unknown>) => {
    createGatewayEventHandler(ctx())({ payload, session_id: 'sess-1', type: 'session.info' } as any)
  }

  it('keeps the model and effort on screen when a frame carries none', async () => {
    const { getUiState } = await import('../app/uiStore.js')
    patchUiState({ info: undefined as any, sid: 'sess-1' })

    info({ model: 'claude-sonnet-5-5', reasoning_effort: 'high', reasoning_effort_wire: 'high', fast: false })
    info({ model: null, reasoning_effort: null, lazy: false })
    info({ model: '', running: false })

    expect(getUiState().info).toMatchObject({ fast: false, model: 'claude-sonnet-5-5', reasoning_effort: 'high', reasoning_effort_wire: 'high' })
  })

  it('still takes a real change, including an empty effort', async () => {
    const { getUiState } = await import('../app/uiStore.js')
    patchUiState({ info: undefined as any, sid: 'sess-1' })

    info({ model: 'a', reasoning_effort: 'high' })
    info({ model: 'b', reasoning_effort: '' })

    expect(getUiState().info).toMatchObject({ model: 'b', reasoning_effort: '' })
  })
})
