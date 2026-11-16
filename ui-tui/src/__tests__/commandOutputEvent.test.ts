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
