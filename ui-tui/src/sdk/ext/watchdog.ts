/**
 * Hang watchdog. A synchronous infinite loop in extension code freezes the whole TUI and no try/catch or
 * timer can interrupt it. The only observer left is another thread: a worker watches a shared "extension
 * code is running since T" slot. When one call stays inside extension code longer than `hangMs` it records the
 * extension in the disabled file and (when `kill` is on) ends the process, so the NEXT start comes up
 * without that extension instead of freezing again. The terminal is reset first; if it still looks odd, run `reset`.
 */
import { Worker } from 'node:worker_threads'

import type { Watchdog } from './runtime.js'

export interface WatchdogOptions {
  hangMs?: number
  /** End the process after recording the hang. Off in tests. */
  kill?: boolean
  pollMs?: number
  /** The `tui-extensions.json` path the worker writes `{ disabled: { id: reason } }` into. */
  statePath: string
}

export interface RunningWatchdog extends Watchdog {
  stop(): void
}

const WORKER_SOURCE = `
const { workerData: d } = require('node:worker_threads')
const fs = require('node:fs')
const since = new Float64Array(d.since)
const len = new Int32Array(d.len)
const bytes = new Uint8Array(d.bytes)
const RESET = '\\x1b[?2004l\\x1b[?1000l\\x1b[?1002l\\x1b[?1003l\\x1b[?1006l\\x1b[?1049l\\x1b[?25h\\x1b[0m'
setInterval(() => {
  const started = since[0]
  if (started === 0 || Date.now() - started < d.hangMs) return
  since[0] = 0
  const id = Buffer.from(bytes.slice(0, Atomics.load(len, 0))).toString('utf8')
  const reason = 'hung the TUI for over ' + d.hangMs + 'ms (watchdog)'
  try {
    let disabled = {}
    try { disabled = JSON.parse(fs.readFileSync(d.statePath, 'utf8')).disabled || {} } catch {}
    fs.mkdirSync(require('node:path').dirname(d.statePath), { recursive: true })
    fs.writeFileSync(d.statePath, JSON.stringify({ disabled: { ...disabled, [id]: reason } }, null, 2) + '\\n')
  } catch {}
  if (d.kill) {
    try { fs.writeSync(1, RESET + '\\nhermes: extension ' + id + ' ' + reason + '; it is disabled for the next start. If the terminal looks odd, run: reset\\n') } catch {}
    process.kill(process.pid, 'SIGKILL')
  }
}, d.pollMs)
`

export const startWatchdog = (options: WatchdogOptions): RunningWatchdog => {
  const since = new Float64Array(new SharedArrayBuffer(8))
  const len = new Int32Array(new SharedArrayBuffer(4))
  const bytes = new Uint8Array(new SharedArrayBuffer(512))

  const worker = new Worker(WORKER_SOURCE, {
    eval: true,
    workerData: {
      bytes: bytes.buffer,
      hangMs: options.hangMs ?? 5000,
      kill: options.kill ?? true,
      len: len.buffer,
      pollMs: options.pollMs ?? 250,
      since: since.buffer,
      statePath: options.statePath
    }
  })

  worker.unref()
  worker.on('error', () => {}) // a dead watchdog must not take the TUI down

  return {
    enter: id => {
      const { written } = new TextEncoder().encodeInto(id.slice(0, 120), bytes)

      Atomics.store(len, 0, written)
      since[0] = Date.now()
    },
    leave: () => {
      since[0] = 0
    },
    stop: () => void worker.terminate()
  }
}
