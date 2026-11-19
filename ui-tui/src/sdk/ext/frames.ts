/**
 * Render an Ink element to the bytes it writes, for tests that prove a slot copy behaves like the core component:
 * the same props and the same keystrokes must write the same bytes (a stronger statement than "the same text").
 * Test-only; imports nothing from vitest.
 */
import { EventEmitter } from 'node:events'

import { renderSync } from '@hermes/ink'
import type { ReactElement } from 'react'

class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = 80
  rows = 24
  isTTY = true
  isRaw = false
  private pending: string[] = []
  ref(): void {}
  unref(): void {}
  read(): string | null {
    return this.pending.shift() ?? null
  }
  send(chunk: string): void {
    this.pending.push(chunk)
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

export interface RenderOptions {
  columns?: number
  /** Raw key sequences typed one after the other, each followed by a settle. */
  keys?: string[]
  rows?: number
  settleMs?: number
}

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

/** The text on screen: a cursor-forward (`ESC [ n C`, how Ink writes a run of spaces) becomes n spaces, the other escapes vanish. */
export const stripAnsi = (text: string): string =>
  text
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\[(\d*)C/g, (_, n: string) => ' '.repeat(Number(n || 1)))
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\[[0-9;?<>=]*[ -/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, '')

/** Everything the element writes to its (fake) terminal until `settleMs` after the last key. */
export const renderOutput = async (element: ReactElement, options: RenderOptions = {}): Promise<string> => {
  const { columns = 80, keys = [], rows = 24, settleMs = 60 } = options
  const stdout = Object.assign(new FakeTty(), { columns, rows })
  const stdin = new FakeTty()
  const stderr = new FakeTty()

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  try {
    await sleep(settleMs)

    for (const key of keys) {
      stdin.send(key)
      await sleep(settleMs)
    }
  } finally {
    instance.unmount()
    instance.cleanup()
  }

  return stdout.chunks.join('')
}

export interface SameRender {
  a: string
  b: string
  same: boolean
  textA: string
  textB: string
}

/** Render two elements the same way and compare the bytes. `textA` and `textB` are for the failure message. */
export const sameRender = async (
  a: ReactElement,
  b: ReactElement,
  options: RenderOptions = {}
): Promise<SameRender> => {
  const outA = await renderOutput(a, options)
  const outB = await renderOutput(b, options)

  return { a: outA, b: outB, same: outA === outB, textA: stripAnsi(outA), textB: stripAnsi(outB) }
}
