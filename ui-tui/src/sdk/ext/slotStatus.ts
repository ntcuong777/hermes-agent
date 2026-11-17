/**
 * What the host leaves on disk about slots, for the person and for the Python side (`plugin.tui-host.status`):
 *   <home>/tui-slots/status.json    the current state of every declared slot (last writer wins: pid and time inside)
 *   <home>/tui-slots/events.jsonl   applied, drift-first-seen, error, auto-disabled, skipped (rotates at 256 KiB)
 *   <home>/tui-slots/notified.json  dedupe of the transcript notices (one per new problem state per 24 h)
 * Every write is best effort: a read-only home never breaks the TUI.
 */
import { appendFileSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import type { SlotEvent, SlotRow } from './runtime.js'
import { defaultStatePath } from './store.js'

export const EVENTS_MAX_BYTES = 262_144
export const NOTIFY_WINDOW_MS = 24 * 3600 * 1000

export interface StatusSink {
  /** True when this key has not been announced in the last 24 hours (and records it). */
  announce(key: string): boolean
  event(event: SlotEvent): void
  snapshot(rows: readonly SlotRow[]): void
}

export const defaultStatusDir = (env: NodeJS.ProcessEnv = process.env): string =>
  join(dirname(defaultStatePath(env)), 'tui-slots')

const quietly = (fn: () => void): void => {
  try {
    fn()
  } catch {
    // best effort
  }
}

const readJson = (path: string): Record<string, unknown> => {
  try {
    const data = JSON.parse(readFileSync(path, 'utf8')) as unknown

    return data && typeof data === 'object' && !Array.isArray(data) ? (data as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

const atomicWrite = (path: string, text: string): void => {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(`${path}.tmp`, text)
  renameSync(`${path}.tmp`, path)
}

export const fileStatusSink = (
  dir: string = defaultStatusDir(),
  now: () => number = Date.now,
  sdk = 1
): StatusSink => ({
  announce: key => {
    const path = join(dir, 'notified.json')
    const seen = readJson(path)
    const last = typeof seen[key] === 'number' ? (seen[key] as number) : 0

    if (now() - last < NOTIFY_WINDOW_MS) {
      return false
    }

    quietly(() => atomicWrite(path, `${JSON.stringify({ ...seen, [key]: now() }, null, 2)}\n`))

    return true
  },
  event: event =>
    quietly(() => {
      const path = join(dir, 'events.jsonl')

      mkdirSync(dir, { recursive: true })

      if ((statSync(path, { throwIfNoEntry: false })?.size ?? 0) > EVENTS_MAX_BYTES) {
        renameSync(path, `${path}.1`)
      }

      appendFileSync(
        path,
        `${JSON.stringify({
          detail: event.detail,
          event: event.kind,
          extension: event.extId,
          slot: event.slot,
          status: event.status,
          ts: new Date(now()).toISOString(),
          upstream_commit: event.upstreamCommit
        })}\n`
      )
    }),
  snapshot: rows =>
    quietly(() =>
      atomicWrite(
        join(dir, 'status.json'),
        `${JSON.stringify(
          {
            pid: process.pid,
            sdk,
            slots: rows.map(row => ({
              detail: row.detail,
              extension: row.extId,
              failures: row.failures,
              id: row.id,
              kind: row.kind,
              props: row.props,
              reason: row.reason,
              status: row.status,
              upstream_commit: row.upstreamCommit
            })),
            surface: 'tui',
            version: 1,
            written_at: new Date(now()).toISOString()
          },
          null,
          2
        )}\n`
      )
    )
})

export const nullStatusSink: StatusSink = { announce: () => true, event: () => {}, snapshot: () => {} }
