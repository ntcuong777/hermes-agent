import { EventEmitter } from 'events'

import React from 'react'
import { describe, expect, it } from 'vitest'

import Text from './components/Text.js'
import Ink from './ink.js'

class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = 40
  rows = 8
  isTTY = true

  write(chunk: string | Uint8Array, cb?: (err?: Error | null) => void): boolean {
    this.chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    cb?.()

    return true
  }
}

function makeInk() {
  const stdout = new FakeTty()
  const stdin = new FakeTty()
  const stderr = new FakeTty()

  const ink = new Ink({
    exitOnCtrlC: false,
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  return { ink, stdout }
}

/** The terminal's cursor-visibility state after every DECSET 25 escape written so far. */
function cursorVisibility(writes: string[]): 'visible' | 'hidden' | 'unknown' {
  let state: 'visible' | 'hidden' | 'unknown' = 'unknown'

  for (const write of writes) {
    const show = write.lastIndexOf('\x1b[?25h')
    const hide = write.lastIndexOf('\x1b[?25l')

    if (show === -1 && hide === -1) {
      continue
    }

    state = show > hide ? 'visible' : 'hidden'
  }

  return state
}

// An external-process round-trip (withInkSuspended — the TUI's Ctrl+G $EDITOR,
// git commit, …) hides the hardware cursor for its repaint burst via ?25l. The
// render path never re-shows it (the declared-cursor park branch only MOVES the
// cursor), so the composer's caret stayed invisible after the editor closed
// until an unrelated effect happened to cycle TextInput's hide-cursor effect.
// Contract: the suspend/resume cycle restores the pre-suspend visibility baseline.
describe('external-process round-trip cursor visibility', () => {
  it('leaves the terminal cursor visible after exitAlternateScreen', () => {
    const { ink, stdout } = makeInk()

    ink.render(React.createElement(Text, null, 'hello'))
    ink.onRender()
    stdout.chunks.length = 0

    ink.enterAlternateScreen()
    expect(cursorVisibility(stdout.chunks)).toBe('visible') // shown for the editor

    stdout.chunks.length = 0
    ink.exitAlternateScreen()

    // The resume hid it for the repaint burst; the contract is that the same
    // write sequence that re-enables reporting also restores visibility.
    expect(stdout.chunks.join('')).toContain('\x1b[?25l')
    expect(cursorVisibility(stdout.chunks)).toBe('visible')

    ink.unmount()
  })
})
