import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'

import { renderSync, Text } from '@hermes/ink'
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'

// The `+new` workspace pick must reach `session.create` as
// `{ cwd, cwd_explicit: true }` on both transports (a deliberate pick beats
// the profile's terminal.cwd), while the dashboard's HERMES_TUI_CWD stays
// non-explicit and a pick overrides it.
//
// HERMES_TUI_CWD is read once at module load (config/env.ts), so the
// precedence test re-imports the lifecycle graph after stubbing — which is
// why the app modules are loaded dynamically here and never statically.

const noop = () => {}

interface LifecycleGraph {
  useSessionLifecycle: any
  getUiState: any
  resetUiState: any
  turnController: any
}

const cleanups: Array<() => void> = []

afterEach(() => {
  while (cleanups.length) {
    cleanups.pop()!()
  }

  vi.unstubAllEnvs()
})

async function loadGraph(env: Record<string, string> = {}): Promise<LifecycleGraph> {
  vi.resetModules()

  for (const [key, value] of Object.entries(env)) {
    vi.stubEnv(key, value)
  }

  const [lifecycle, uiStore, turnController] = await Promise.all([
    import('../app/useSessionLifecycle.js'),
    import('../app/uiStore.js'),
    import('../app/turnController.js')
  ])

  return {
    useSessionLifecycle: lifecycle.useSessionLifecycle,
    getUiState: uiStore.getUiState,
    resetUiState: uiStore.resetUiState,
    turnController: turnController.turnController
  }
}

async function mount(gw: any, graph: LifecycleGraph) {
  const home = mkdtempSync(join(tmpdir(), 'ink-ws-pick-'))
  vi.stubEnv('HERMES_HOME', home)
  let lifecycle: any

  function Harness() {
    lifecycle = graph.useSessionLifecycle({
      colsRef: { current: 80 }, composerActions: { setComposerTokens: noop }, gw, rpc: gw.request,
      scrollRef: { current: null }, panel: noop, sys: noop, setHistoryItems: noop, setLastUserMsg: noop,
      setSessionStartedAt: noop, setStickyPrompt: noop, setVoiceProcessing: noop, setVoiceRecording: noop
    } as any)

    return <Text>probe</Text>
  }

  const instance = renderSync(<Harness />, {
    stdin: new PassThrough() as any,
    stdout: Object.assign(new PassThrough(), { columns: 80, rows: 20, isTTY: false }) as any,
    stderr: new PassThrough() as any, patchConsole: false
  })

  const cleanup = () => {
    instance.unmount()
    graph.turnController.fullReset()
    graph.resetUiState()
    rmSync(home, { recursive: true, force: true })
  }

  cleanups.push(cleanup)

  return { get lifecycle() { return lifecycle }, request: gw.request, cleanup }
}

function freshRequest() {
  return vi.fn(async (method: string, params: any) => {
    if (method === 'session.create') {
      return { session_id: 'fresh', subscription_id: 'sub-fresh', info: { model: 'test', skills: {}, tools: {}, stored_session_id: 'fresh' } }
    }

    if (method === 'setup.status') {
      return { provider_configured: true }
    }

    return { ...params }
  })
}

it('sends a picked workspace as explicit cwd on session.create (canonical)', async () => {
  const graph = await loadGraph()
  const request = freshRequest()
  const h = await mount({ isCanonical: true, request }, graph)

  await h.lifecycle.newLiveSession(undefined, undefined, '/home/u/proj')
  expect(graph.getUiState().sid).toBe('fresh')
  expect(request).toHaveBeenCalledWith(
    'session.create',
    expect.objectContaining({ cwd: '/home/u/proj', cwd_explicit: true })
  )
})

it('sends a picked workspace on the legacy transport too, overriding HERMES_TUI_CWD', async () => {
  const graph = await loadGraph({ HERMES_TUI_CWD: '/dashboard/workspace' })
  const request = freshRequest()
  const h = await mount({ isCanonical: false, request }, graph)

  await h.lifecycle.newLiveSession(undefined, undefined, '  /x  ')
  expect(request).toHaveBeenCalledWith(
    'session.create',
    expect.objectContaining({ cols: 80, cwd: '/x', cwd_explicit: true })
  )
})

it('without a pick the launch workspace stays non-explicit', async () => {
  const graph = await loadGraph({ HERMES_TUI_CWD: '/dashboard/workspace' })
  const request = freshRequest()
  const h = await mount({ isCanonical: false, request }, graph)

  await h.lifecycle.newLiveSession()
  expect(request).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/dashboard/workspace' })

  await h.lifecycle.newLiveSession(undefined, undefined, '   ')
  expect(request).toHaveBeenLastCalledWith('session.create', { cols: 80, cwd: '/dashboard/workspace' })
})
