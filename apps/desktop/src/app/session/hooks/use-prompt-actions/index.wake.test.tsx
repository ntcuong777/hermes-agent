import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getLatestSessionMessages } from '@/hermes'
import { setSessions } from '@/store/session'
import { $wakeWord, resetWakeWordState } from '@/store/wake-word'

import { actRender, Harness, type HarnessHandle, sessionInfo } from './index-test-utils'
import { clearStoredJournal } from './prepared-submissions-test-utils'
import { clearSingleFlightSessionResumeState } from './single-flight-resume'

// Suites in this file reuse the same stored-id constants. The module-level single-flight resume map
// (and drift-recovery cache) would otherwise leak a never-settling in-flight promise from one test into the next.
beforeEach(() => {
  clearSingleFlightSessionResumeState()
  clearStoredJournal()
  // Queue mutations build on the persisted map, not the atom — a queue an
  // earlier test left in storage would otherwise sit ahead of this test's send.
  window.localStorage.removeItem('hermes.desktop.composerQueue.v1')
  vi.mocked(getLatestSessionMessages).mockReset()
  vi.mocked(getLatestSessionMessages).mockImplementation(async () => ({ messages: [], session_id: 'session' }))
})

vi.mock('@/hermes', () => ({
  getLatestSessionMessages: vi.fn(async () => ({ messages: [], session_id: 'session' })),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  getSession: vi.fn(),
  PROMPT_SUBMIT_REQUEST_TIMEOUT_MS: 1_800_000,
  setApiRequestProfile: vi.fn(),
  transcribeAudio: vi.fn()
}))

vi.mock('@/store/gateway', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  requestGatewayForAgent: vi.fn()
}))

// Every typed command also fires this (fire-and-forget); these tests assert the command's own traffic.
const SLASH_METRIC = 'shared_metrics.slash_command'

// Helper: extract rendered text parts from captured updateSessionState seeds.
function renderedSeedTexts(seeds: Record<string, unknown>[]): string[] {
  return seeds.flatMap(state => {
    const messages = Array.isArray(state.messages)
      ? (state.messages as Array<{ parts?: Array<{ text?: string }> }>)
      : []

    return messages.flatMap(message => (message.parts ?? []).map(part => part.text ?? ''))
  })
}

describe('usePromptActions /wake', () => {
  beforeEach(() => {
    setSessions(() => [sessionInfo()])
    resetWakeWordState()
  })

  afterEach(() => {
    cleanup()
    resetWakeWordState()
    vi.restoreAllMocks()
  })

  it('starts the GUI-owned listener through wake.start and never spawns the slash worker', async () => {
    const seeds: Record<string, unknown>[] = []

    const requestGateway = vi.fn(async (method: string, _params?: Record<string, unknown>, _timeoutMs?: number) => {
      if (method === 'wake.start') {
        return {
          owner_surface: 'gui',
          phrase: 'hey hermes',
          provider: 'openwakeword',
          started: true
        } as never
      }

      if (method === 'wake.status') {
        return {
          available: true,
          configured_surface: 'gui',
          enabled: true,
          input_device: {
            hostapi: 'Windows WASAPI',
            name: 'Microphone Array',
            selector: 'Microphone Array'
          },
          listening: true,
          owner_surface: 'gui',
          phrase: 'hey hermes',
          provider: 'openwakeword'
        } as never
      }

      return {} as never
    })

    let handle: HarnessHandle | null = null
    await actRender(
      <Harness
        onReady={h => (handle = h)}
        onSeedState={state => seeds.push(state)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
      />
    )

    await handle!.submitText('/wake on')

    expect(requestGateway).toHaveBeenCalledWith(
      'wake.start',
      { client_capture: true, persist: true, surface: 'gui' },
      180_000
    )
    expect(requestGateway).toHaveBeenCalledWith('wake.status', { client_capture: true, surface: 'gui' })
    expect(requestGateway).not.toHaveBeenCalledWith('slash.exec', expect.anything())
    expect(requestGateway).not.toHaveBeenCalledWith('command.dispatch', expect.anything())
    expect($wakeWord.get()).toMatchObject({ available: true, enabled: true, listening: true })
    expect(renderedSeedTexts(seeds).join('\n')).toContain('Input: Microphone Array (Windows WASAPI)')
  })

  it('uses gateway truth for a bare toggle and stops through wake.stop', async () => {
    let statusCalls = 0

    const requestGateway = vi.fn(async (method: string) => {
      if (method === 'wake.status') {
        statusCalls += 1

        return {
          available: true,
          enabled: statusCalls === 1,
          listening: statusCalls === 1,
          owner_surface: statusCalls === 1 ? 'gui' : null,
          phrase: 'hey hermes',
          provider: 'openwakeword'
        } as never
      }

      if (method === 'wake.stop') {
        return { disabled_persisted: true, stopped: true } as never
      }

      return {} as never
    })

    let handle: HarnessHandle | null = null
    await actRender(
      <Harness onReady={h => (handle = h)} refreshSessions={async () => undefined} requestGateway={requestGateway} />
    )

    await handle!.submitText('/wake')

    expect(requestGateway.mock.calls.map(([method]) => method).filter(m => m !== SLASH_METRIC)).toEqual([
      'wake.status',
      'wake.stop',
      'wake.status'
    ])
    expect(requestGateway).toHaveBeenCalledWith('wake.stop', { persist: true })
    expect(requestGateway).not.toHaveBeenCalledWith('slash.exec', expect.anything())
    expect(requestGateway).not.toHaveBeenCalledWith('command.dispatch', expect.anything())
    expect($wakeWord.get()).toMatchObject({ enabled: false, listening: false })
  })
})
