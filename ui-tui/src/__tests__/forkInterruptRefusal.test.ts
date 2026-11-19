/** fork: the client side of the gateway's structured interrupt refusal. The gateway (fork,
 *  `gateway/session_pending_controls.py`) answers an interrupt of a busy turn with a raw
 *  `session_busy` error carrying `data.reason`; this pins that the TUI renders it as-is —
 *  the stock "wait for it to finish, then try again" copy would name exactly the reply the
 *  user is interrupting. Lives outside upstream's test file: it pins fork-only gateway
 *  behaviour against core's own (upstream) rendering code. */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'
import type { Msg } from '../types.js'

const ref = <T>(current: T) => ({ current })

const buildCtx = (appended: Msg[]) =>
  ({
    composer: {
      dequeue: () => undefined,
      queueEditRef: ref<null | number>(null),
      sendQueued: vi.fn(),
      setInput: vi.fn()
    },
    gateway: {
      gw: { request: vi.fn(async () => null) },
      rpc: vi.fn(async () => null)
    },
    session: {
      STARTUP_RESUME_ID: '',
      colsRef: ref(80),
      newSession: vi.fn(),
      resetSession: vi.fn(),
      resumeById: vi.fn(),
      setCatalog: vi.fn()
    },
    submission: {
      submitRef: { current: vi.fn() }
    },
    system: {
      bellOnComplete: false,
      sys: vi.fn()
    },
    transcript: {
      appendMessage: (msg: Msg) => appended.push(msg),
      panel: (title: string, sections: any[]) =>
        appended.push({ kind: 'panel', panelData: { sections, title }, role: 'system', text: '' }),
      setHistoryItems: vi.fn()
    },
    voice: {
      setProcessing: vi.fn(),
      setRecording: vi.fn(),
      setVoiceEnabled: vi.fn()
    }
  }) as any

describe('the structured interrupt refusal stays raw', () => {
  beforeEach(() => {
    resetUiState()
    resetTurnState()
    turnController.fullReset()
  })

  it('keeps a structured interrupt refusal raw, where the refusal copy would contradict the interrupt', async () => {
    vi.useFakeTimers()

    try {
      turnController.fullReset()
      const appended: Msg[] = []
      const ctx = buildCtx(appended)
      let reject!: (error: Error) => void
      ctx.gateway.gw.isCanonical = true
      ctx.gateway.gw.request = vi.fn(
        () =>
          new Promise((_, fail) => {
            reject = fail
          })
      )
      const onEvent = createGatewayEventHandler(ctx)
      const info = { model: 'test', tools: {}, skills: {}, execution_epoch: 'owner', execution_generation: 2 }
      patchUiState({ sid: 'sess-1', info })

      const emit = (type: string, payload = {}) =>
        onEvent({ type, session_id: 'sess-1', payload: { ...info, ...payload } } as any)

      emit('message.start')
      emit('message.delta', { text: 'partial' })

      const pending = turnController.interruptTurn(
        {
          appendMessage: ctx.transcript.appendMessage,
          gw: ctx.gateway.gw,
          sid: 'sess-1',
          sys: ctx.system.sys
        },
        { keepBusy: true }
      )

      // The user is interrupting the running reply: "wait for it to finish, then
      // try again" (the session_busy rewrite) would name exactly that reply.
      reject(Object.assign(new Error('session_busy'), { data: { reason: 'session_busy' } }))
      await pending

      expect(ctx.system.sys).toHaveBeenCalledExactlyOnceWith('interrupt failed: session_busy')
    } finally {
      turnController.fullReset()
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })
})
