import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ActiveSessionSwitcher, sessionListWorkspaceParams } from '../components/activeSessionSwitcher.js'
import { DEFAULT_THEME } from '../theme.js'

describe('sessionListWorkspaceParams', () => {
  it('sends the workspace only while the filter is on', () => {
    expect(sessionListWorkspaceParams(false, '/work/repo')).toEqual({})
    expect(sessionListWorkspaceParams(true, '/work/repo')).toEqual({ cwd: '/work/repo' })
    expect(sessionListWorkspaceParams(true, '')).toEqual({})
  })
})

const flush = () => new Promise(resolve => setImmediate(resolve))

async function until(condition: () => boolean, attempts = 100) {
  for (let i = 0; i < attempts && !condition(); i++) {
    await new Promise(resolve => setTimeout(resolve, 10))
  }
}

it('w narrows the resumable list to this workspace and back', async () => {
  const request = vi.fn(async (method: string) => {
    if (method !== 'session.list') {
      throw new Error('invalid_params')
    }

    return {
      scope: 'live',
      sessions: [
        {
          id: 'owner',
          session_id: 'owner',
          title: 'Owned conversation',
          started_at: 1,
          message_count: 2,
          running: true
        }
      ]
    }
  })

  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: vi.fn(), ref: vi.fn(), unref: vi.fn() })
  let output = ''
  const stdout = Object.assign(new PassThrough(), { columns: 120, rows: 30, isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const props = {
    currentSessionId: 'owner',
    gw: { isCanonical: true, request } as any,
    onCancel: vi.fn(),
    onClose: vi.fn(),
    onNew: vi.fn(),
    onNewPrompt: vi.fn(),
    onResume: vi.fn(),
    onSelect: vi.fn(),
    t: DEFAULT_THEME
  }

  const instance = renderSync(<ActiveSessionSwitcher {...props} />, {
    stdin: stdin as any,
    stdout: stdout as any,
    stderr: new PassThrough() as any,
    patchConsole: false
  })

  try {
    await until(() => output.includes('▸  1.  current'))
    // A terminal answers Ink's startup queries (DA1 `ESC[c`); until then Ink holds keyboard input.
    stdin.write('\u001b[?62;c')
    // Ink reads the reply asynchronously; only after it settles does a key reach the switcher.
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(output).toContain('this workspace')

    stdin.write('w')
    await until(() => output.includes('all sessions'))

    expect(output).toContain('all sessions')
    expect(request).toHaveBeenLastCalledWith('session.list', expect.objectContaining({ cwd: process.cwd() }))

    stdin.write('w')
    await until(() => output.includes('this workspace · ', output.lastIndexOf('all sessions')))
    await flush()
    await flush()

    const last = request.mock.calls.at(-1)?.[1] as Record<string, unknown>
    expect(last).not.toHaveProperty('cwd')
  } finally {
    instance.unmount()
  }
})
