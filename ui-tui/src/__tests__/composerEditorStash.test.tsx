import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'

import { renderSync, Text } from '@hermes/ink'
import React from 'react'
import { expect, it, vi } from 'vitest'

import { patchUiState, resetUiState } from '../app/uiStore.js'
import { useComposerState } from '../app/useComposerState.js'

const editor = vi.hoisted(() => ({ run: (_file: string) => {} }))

vi.mock('node:child_process', async orig => ({
  ...(await orig<typeof import('node:child_process')>()),
  spawnSync: vi.fn((_cmd: string, args: string[]) => {
    editor.run(args.at(-1)!)

    return { status: 0 }
  })
}))
vi.mock('../lib/editor.js', async orig => ({
  ...(await orig<typeof import('../lib/editor.js')>()),
  resolveEditor: () => ['fake-editor']
}))
vi.mock('@hermes/ink', async orig => ({
  ...(await orig<typeof import('@hermes/ink')>()),
  withInkSuspended: (fn: () => Promise<void>) => fn()
}))

function mount() {
  let composer!: ReturnType<typeof useComposerState>

  function Harness() {
    composer = useComposerState({
      gw: { request: vi.fn(() => Promise.resolve({})) },
      submitRef: { current: vi.fn() },
      sys: vi.fn()
    } as any)

    return <Text>composer</Text>
  }

  const instance = renderSync(<Harness />, {
    stdin: new PassThrough() as any,
    stdout: Object.assign(new PassThrough(), { columns: 80, rows: 20, isTTY: false }) as any,
    stderr: new PassThrough() as any,
    patchConsole: false
  })

  return { instance, get: () => composer }
}

it('parks an editor draft when the session changed, restores it unsent on return', async () => {
  const home = mkdtempSync(join(tmpdir(), 'ink-editor-stash-'))
  vi.stubEnv('HERMES_HOME', home)
  resetUiState()
  patchUiState({ sid: 'a' })
  const { instance, get } = mount()

  try {
    get().actions.setInput('before')
    editor.run = file => {
      writeFileSync(file, 'edited\nsecond')
      patchUiState({ sid: 'b' })
      get().actions.clearIn()
    }
    await get().actions.openEditor()

    // Session b is current: its composer is left alone, nothing was submitted.
    expect(get().refs.inputRef.current).toBe('')
    expect(get().state.inputBuf).toEqual([])

    patchUiState({ sid: 'a' })
    await vi.waitFor(() => expect(get().state.input).toBe('second'))
    expect(get().state.inputBuf).toEqual(['edited'])
  } finally {
    instance.unmount()
    resetUiState()
    vi.unstubAllEnvs()
    rmSync(home, { recursive: true, force: true })
  }
})

it('holds an editor draft back while the composer has newer text', async () => {
  const home = mkdtempSync(join(tmpdir(), 'ink-editor-stash-'))
  vi.stubEnv('HERMES_HOME', home)
  resetUiState()
  patchUiState({ sid: 'a' })
  const { instance, get } = mount()

  try {
    get().actions.setInput('before')
    editor.run = file => {
      writeFileSync(file, 'edited')
      get().actions.setInput('typed meanwhile')
    }
    await get().actions.openEditor()
    expect(get().refs.inputRef.current).toBe('typed meanwhile')

    get().actions.clearIn()
    await vi.waitFor(() => expect(get().state.input).toBe('edited'))
  } finally {
    instance.unmount()
    resetUiState()
    vi.unstubAllEnvs()
    rmSync(home, { recursive: true, force: true })
  }
})
