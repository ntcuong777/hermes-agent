import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ActiveSessionSwitcher } from '../components/activeSessionSwitcher.js'
import { DEFAULT_THEME } from '../theme.js'

// Ctrl+O on the `+new` row swaps the prompt input for a workspace path input;
// the confirmed pick rides both dispatch paths (blank new and prompt dispatch)
// and Esc steps back without changing it.
//
// The rendered output is an append-only ANSI stream (Ink repaints move the
// cursor and rewrite frames), so "gone" is asserted as "never re-emitted after
// its last occurrence" (`tailAfter`) and mode switches as lastIndexOf ordering
// — not plain `not.toContain`, which older frames always fail.

async function until(condition: () => boolean, attempts = 100) {
  for (let i = 0; i < attempts && !condition(); i++) {
    await new Promise(resolve => setTimeout(resolve, 10))
  }
}

const CTRL_O = '\u000f'

function makeRequest() {
  return vi.fn(async (method: string) => {
    if (method !== 'session.list') {
      throw new Error('invalid_params')
    }

    return {
      scope: 'live',
      sessions: [{ id: 'liv1', title: 'Deploy pipeline', running: true }]
    }
  })
}

async function renderSwitcherOnNewRow() {
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

  await until(() => output.includes('\u25b8  1.  current'))
  // A terminal answers Ink's startup queries (DA1 `ESC[c`); until then Ink holds keyboard input.
  stdin.write('\u001b[?62;c')
  await new Promise(resolve => setTimeout(resolve, 50))
  // Up from the live row to the pinned `+new` row.
  stdin.write('\u001b[A')
  await until(() => output.includes('prompt \u203a '))

  return { props, stdin, instance, text: () => output }
}

/** Everything re-emitted after the last occurrence of `probe`: UI state that
 *  is really gone never reappears in this tail, because every repaint emits
 *  only the current frame. */
const tailAfter = (text: string, probe: string) => {
  const at = text.lastIndexOf(probe)

  return at < 0 ? '' : text.slice(at + probe.length)
}

describe('sessions overlay workspace pick', () => {
  it('picks a workspace and uses it for a blank new session', async () => {
    const { props, stdin, instance, text } = await renderSwitcherOnNewRow()

    try {
      stdin.write(CTRL_O)
      await until(() => text().includes('workspace \u203a '))

      stdin.write('~/proj')
      // Enter commits (the switcher trims through its key branch) and returns
      // to the prompt input.
      stdin.write('\r')
      await until(() => tailAfter(text(), '~/proj').includes('prompt \u203a '))
      await until(() => tailAfter(text(), '~/proj').includes('workspace: ~/proj'))

      // Blank new (empty prompt) starts the session in the picked workspace.
      stdin.write('\r')
      await until(() => props.onNew.mock.calls.length > 0)
      expect(props.onNew).toHaveBeenCalledWith('~/proj')
    } finally {
      instance.unmount()
    }
  })

  it('closes the editor with Ctrl+O and dispatches the prompt with the stored pick', async () => {
    const { props, stdin, instance, text } = await renderSwitcherOnNewRow()

    try {
      stdin.write(CTRL_O)
      await until(() => text().includes('workspace \u203a '))
      stdin.write('~/proj\r')
      await until(() => tailAfter(text(), '~/proj').includes('prompt \u203a '))

      // Reopen, abandon an edit with Ctrl+O (not Esc), dispatch the prompt:
      // carrying '~/proj' proves the closed editor left the pick untouched.
      stdin.write(CTRL_O)
      await until(() => text().lastIndexOf('workspace \u203a ') > text().lastIndexOf('prompt \u203a '))
      stdin.write('/tmp/other')
      await new Promise(resolve => setTimeout(resolve, 60))
      stdin.write(CTRL_O)
      await until(() => text().lastIndexOf('prompt \u203a ') > text().lastIndexOf('workspace \u203a '))

      stdin.write('fix the bug')
      stdin.write('\r')
      await until(() => props.onNewPrompt.mock.calls.length > 0)
      expect(props.onNewPrompt).toHaveBeenCalledWith('fix the bug', undefined, '~/proj')
    } finally {
      instance.unmount()
    }
  })

  it('steps back to the prompt on Esc without changing the pick', async () => {
    const { props, stdin, instance, text } = await renderSwitcherOnNewRow()

    try {
      stdin.write(CTRL_O)
      await until(() => text().includes('workspace \u203a '))
      stdin.write('~/proj\r')
      await until(() => tailAfter(text(), '~/proj').includes('prompt \u203a '))

      // Reopen and step back with a clean-chunk Esc: the pick stays, nothing
      // dispatches on the way out.
      stdin.write(CTRL_O)
      await until(() => text().lastIndexOf('workspace \u203a ') > text().lastIndexOf('prompt \u203a '))
      await new Promise(resolve => setTimeout(resolve, 60))
      stdin.write('\u001b')
      await until(() => text().lastIndexOf('prompt \u203a ') > text().lastIndexOf('workspace \u203a '))
      expect(props.onNew).not.toHaveBeenCalled()
      expect(props.onNewPrompt).not.toHaveBeenCalled()
      expect(props.onCancel).not.toHaveBeenCalled()

      // The pick survived the step-back: a blank new uses it.
      stdin.write('\r')
      await until(() => props.onNew.mock.calls.length > 0)
      expect(props.onNew).toHaveBeenCalledWith('~/proj')
    } finally {
      instance.unmount()
    }
  })

  it('clears the pick with an empty confirm', async () => {
    const { props, stdin, instance, text } = await renderSwitcherOnNewRow()

    try {
      stdin.write(CTRL_O)
      await until(() => text().includes('workspace \u203a '))
      stdin.write('~/proj\r')
      await until(() => tailAfter(text(), '~/proj').includes('prompt \u203a '))

      // The pick re-opens pre-filled; Ctrl+U empties the input so the empty
      // confirm clears it.
      stdin.write(CTRL_O)
      await until(() => text().lastIndexOf('workspace \u203a ') > text().lastIndexOf('prompt \u203a '))
      stdin.write('\u0015')
      stdin.write('\r')
      await until(() => tailAfter(text(), 'workspace: ~/proj').includes('prompt \u203a '))
      expect(tailAfter(text(), 'workspace: ~/proj')).not.toContain('workspace: ~/proj')

      stdin.write('\r')
      await until(() => props.onNew.mock.calls.length > 0)
      expect(props.onNew).toHaveBeenCalledWith(undefined)
    } finally {
      instance.unmount()
    }
  })
})
