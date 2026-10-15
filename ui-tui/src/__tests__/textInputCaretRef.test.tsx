import { EventEmitter } from 'events'

import { renderSync } from '@hermes/ink'
import React, { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TextInput } from '../components/textInput.js'
import type { InputCursorSnapshot } from '../components/textInput.js'

// The composer completes `@` where the caret is, so TextInput must say where the caret is (with every value it
// hands up), and must keep the caret where the parent puts it after splicing a completion into the middle.
// Harness copied from textInputStaleParentEcho.test.tsx.

class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = 80
  rows = 24
  isTTY = true
  isRaw = false
  private pendingReads: string[] = []
  ref(): void {}
  unref(): void {}
  read(): string | null {
    return this.pendingReads.shift() ?? null
  }
  send(chunk: string): void {
    this.pendingReads.push(chunk)
    this.emit('readable')
  }
  setEncoding(): this {
    return this
  }
  setRawMode(mode: boolean): this {
    this.isRaw = mode

    return this
  }
  write(chunk: string | Uint8Array, cb?: (err?: Error | null) => void): boolean {
    this.chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    cb?.()

    return true
  }
}

const tick = () => new Promise<void>(resolve => setImmediate(resolve))
const LEFT = '\u001b[D'

function Harness({
  caretRef,
  onValue,
  setValueRef
}: {
  caretRef: React.MutableRefObject<InputCursorSnapshot | null>
  onValue: (value: string) => void
  setValueRef: React.MutableRefObject<((next: string) => void) | null>
}) {
  const [value, setValue] = useState('')
  setValueRef.current = setValue

  return React.createElement(TextInput, {
    caretRef,
    onChange: (next: string) => {
      setValue(next)
      onValue(next)
    },
    value
  })
}

describe('TextInput caretRef', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'Date'] })
    ;(process.stdout as { isTTY?: boolean }).isTTY = true
    vi.spyOn(process.stdout, 'write').mockImplementation(() => true)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    ;(process.stdout as { isTTY?: boolean }).isTTY = undefined
  })

  type Io = {
    caretRef: React.MutableRefObject<InputCursorSnapshot | null>
    setValueRef: React.MutableRefObject<((next: string) => void) | null>
    settle: () => Promise<void>
    stdin: FakeTty
  }

  async function run(steps: (io: Io) => Promise<void>) {
    const stdout = new FakeTty()
    const stdin = new FakeTty()
    const stderr = new FakeTty()
    const values: string[] = []
    const caretRef: React.MutableRefObject<InputCursorSnapshot | null> = { current: null }
    const setValueRef: React.MutableRefObject<((next: string) => void) | null> = { current: null }

    const instance = renderSync(React.createElement(Harness, { caretRef, onValue: v => values.push(v), setValueRef }), {
      patchConsole: false,
      stderr: stderr as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream
    })

    const settle = async () => {
      await tick()
      vi.advanceTimersByTime(20)
      await tick()
      await tick()
    }

    try {
      await tick()
      await steps({ caretRef, setValueRef, settle, stdin })
      await settle()
    } finally {
      instance.unmount()
      instance.cleanup()
    }

    return { caretRef, setValueRef, values }
  }

  it('reports the caret with a value typed in the middle', async () => {
    const { caretRef, values } = await run(async ({ stdin, settle }) => {
      for (const ch of ['a', 'b']) {
        stdin.send(ch)
        await settle()
      }

      stdin.send(LEFT)
      await settle()
      stdin.send('@')
      await settle()
    })

    expect(values.at(-1)).toBe('a@b')
    expect(caretRef.current).toEqual({ cursor: 2, value: 'a@b' })
  })

  it('keeps the caret where the parent puts it for a new value, then types there', async () => {
    const { values } = await run(async ({ caretRef, setValueRef, settle, stdin }) => {
      stdin.send('a')
      await settle()
      stdin.send('b')
      await settle()
      // Splice `@file:` in after "a", the way Tab applies a completion mid-prompt.
      caretRef.current = { cursor: 7, value: 'a@file:b' }
      setValueRef.current!('a@file:b')
      await settle()
      stdin.send('X')
      await settle()
    })

    expect(values.at(-1)).toBe('a@file:Xb')
  })

  it('a new value with no caret for it puts the caret at the end, as before', async () => {
    const { values } = await run(async ({ setValueRef, settle, stdin }) => {
      stdin.send('a')
      await settle()
      setValueRef.current!('history line')
      await settle()
      stdin.send('X')
      await settle()
    })

    expect(values.at(-1)).toBe('history lineX')
  }) // a new value with no caret
}) // TextInput caretRef
