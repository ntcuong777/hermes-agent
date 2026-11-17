import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ActiveSessionSwitcher } from '../components/activeSessionSwitcher.js'
import { DEFAULT_THEME } from '../theme.js'

const flush = () => new Promise(resolve => setImmediate(resolve))

async function until(condition: () => boolean, attempts = 100) {
  for (let i = 0; i < attempts && !condition(); i++) {
    await new Promise(resolve => setTimeout(resolve, 10))
  }
}

const LIVE = [{ id: 'liv1', title: 'Deploy pipeline', running: true }]

const HISTORY = [
  {
    id: 'aaa',
    title: 'Payments refactor',
    preview: 'stripe webhooks',
    cwd: '/home/u/pay',
    git_branch: 'feat/pay',
    started_at: 1,
    message_count: 4
  },
  {
    id: 'bbb',
    title: 'Random chat',
    preview: 'hello there',
    cwd: '/home/u/misc',
    git_branch: 'main',
    started_at: 2,
    message_count: 9
  }
]

function makeRequest() {
  return vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method !== 'session.list') {
      throw new Error('invalid_params')
    }

    if (params?.scope === 'stored') {
      return { scope: 'stored', sessions: HISTORY }
    }

    return { scope: 'live', sessions: LIVE }
  })
}

async function renderSwitcher() {
  const request = makeRequest()
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: vi.fn(), ref: vi.fn(), unref: vi.fn() })
  let output = ''
  const stdout = Object.assign(new PassThrough(), { columns: 120, rows: 30, isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const props = {
    currentSessionId: 'liv1',
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

  await until(() => output.includes('▸  1.  current'))
  // A terminal answers Ink's startup queries (DA1 `ESC[c`); until then Ink holds keyboard input.
  stdin.write('\u001b[?62;c')
  await new Promise(resolve => setTimeout(resolve, 50))

  return { props, stdin, instance, text: () => output, mark: () => output.length }
}

describe('sessions overlay filter', () => {
  it('opens with /, narrows rows as you type, resumes on Enter, Esc clears then backs out', async () => {
    const { props, stdin, instance, text, mark } = await renderSwitcher()

    try {
      // `/` opens the filter input (hotkeys d/w and plain chars are suppressed while open).
      stdin.write('/')
      await until(() => text().includes('find › '))

      // Typing narrows to the matching history row; the other rows disappear.
      const before = mark()
      stdin.write('pay')
      await until(() => !text().slice(before).includes('bbb'))
      expect(props.onResume).not.toHaveBeenCalled()

      // `d` feeds the query instead of arming a delete.
      stdin.write('d')
      await until(() => text().slice(before).includes('no sessions match'))
      expect(text().slice(before)).not.toContain('press d again')

      // Esc clears the query; rows return and the filter stays open.
      stdin.write('\u001b')
      await until(() => text().slice(before).includes('Random chat'))
      expect(props.onCancel).not.toHaveBeenCalled()

      // Esc-clearing clamped the selection onto the pinned +new row, where `/`
      // deliberately does not open the filter (C3) — move off it first.
      stdin.write('\u001b[B')

      // Esc again leaves filter mode (the input line stops repainting), overlay still open.
      const beforeExit = mark()
      stdin.write('\u001b')
      await until(() => !text().slice(beforeExit).includes('find › '))
      expect(props.onCancel).not.toHaveBeenCalled()

      // Enter resumes the selected row. Reopen, retype, Enter on the history row.
      stdin.write('/')
      await until(() => text().slice(beforeExit).includes('find › '))
      stdin.write('pay')
      const beforeEnter = mark()
      await until(() => !text().slice(beforeEnter).includes('bbb'))
      stdin.write('\r')
      await until(() => props.onResume.mock.calls.length > 0)
      expect(props.onResume).toHaveBeenCalledWith('aaa')

      // Filter mode closed with Enter, so the next Esc closes the overlay itself.
      await flush()
      stdin.write('\u001b')
      await until(() => props.onCancel.mock.calls.length > 0)
    } finally {
      instance.unmount()
    }
  })

  it('keeps the query when Enter leaves filter mode and applies it on reopen', async () => {
    const { props, stdin, instance, text, mark } = await renderSwitcher()

    try {
      stdin.write('/')
      await until(() => text().includes('find › '))

      const before = mark()
      stdin.write('pay')
      await until(() => !text().slice(before).includes('bbb'))

      // Enter on the only visible (history) row: resumes AND exits filter mode.
      stdin.write('\r')
      await until(() => props.onResume.mock.calls.length > 0)
      expect(props.onResume).toHaveBeenCalledWith('aaa')

      // Reopening shows the retained query already applied.
      const reopened = mark()
      stdin.write('/')
      await until(() => text().slice(reopened).includes('find › '))
      expect(text().slice(reopened)).not.toContain('Random chat')
    } finally {
      instance.unmount()
    }
  })
  it('feeds w into the query instead of toggling the workspace listing', async () => {
    const { props, stdin, instance, text, mark } = await renderSwitcher()

    try {
      stdin.write('/')
      await until(() => text().includes('find › '))

      // `w` while filtering must reach the query, not toggle the gateway-side
      // workspace listing (no session.list call gains the cwd param).
      const before = mark()
      stdin.write('w')
      await until(() => text().slice(before).includes('find › w'))

      for (const call of props.gw.request.mock.calls) {
        expect(call[1]).not.toHaveProperty('cwd')
      }

      // …and it did not arm a delete either.
      expect(text().slice(before)).not.toContain('press d again')
    } finally {
      instance.unmount()
    }
  })
})
