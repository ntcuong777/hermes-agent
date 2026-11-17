import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import type { SlotEvent, SlotRow } from '../../sdk/ext/runtime.js'
import { EVENTS_MAX_BYTES, fileStatusSink, NOTIFY_WINDOW_MS } from '../../sdk/ext/slotStatus.js'

const row: SlotRow = {
  detail: 'upstream changed',
  extId: 'p/x',
  failures: 0,
  id: 'sessionSwitcher',
  kind: 'component',
  props: 1,
  reason: 'why',
  status: 'skipped-drift',
  upstreamCommit: 'abcdef0123'
}

const event: SlotEvent = {
  detail: 'd',
  extId: 'p/x',
  kind: 'drift-first-seen',
  slot: 'sessionSwitcher',
  status: 'skipped-drift',
  upstreamCommit: 'abcdef0123'
}

const dir = () => join(mkdtempSync(join(tmpdir(), 'slot-status-')), 'tui-slots')

describe('fileStatusSink', () => {
  it('writes status.json with the pid, the surface and one entry per slot (the shape the Python side reads)', () => {
    const d = dir()

    fileStatusSink(d, () => Date.parse('2026-11-17T10:00:00Z')).snapshot([row])

    expect(JSON.parse(readFileSync(join(d, 'status.json'), 'utf8'))).toEqual({
      pid: process.pid,
      sdk: 1,
      slots: [
        {
          detail: 'upstream changed',
          extension: 'p/x',
          failures: 0,
          id: 'sessionSwitcher',
          kind: 'component',
          props: 1,
          reason: 'why',
          status: 'skipped-drift',
          upstream_commit: 'abcdef0123'
        }
      ],
      surface: 'tui',
      version: 1,
      written_at: '2026-11-17T10:00:00.000Z'
    })
  })

  it('appends one json line per event with the registry event names', () => {
    const d = dir()
    const sink = fileStatusSink(d, () => Date.parse('2026-11-17T10:00:00Z'))

    sink.event(event)
    sink.event({ ...event, kind: 'applied', status: 'active' })

    const lines = readFileSync(join(d, 'events.jsonl'), 'utf8')
      .trim()
      .split('\n')
      .map(line => JSON.parse(line))

    expect(lines.map(l => l.event)).toEqual(['drift-first-seen', 'applied'])
    expect(lines[0]).toMatchObject({
      extension: 'p/x',
      slot: 'sessionSwitcher',
      ts: '2026-11-17T10:00:00.000Z',
      upstream_commit: 'abcdef0123'
    })
  })

  it('rotates events.jsonl to events.jsonl.1 past the size limit', () => {
    const d = dir()
    const sink = fileStatusSink(d)

    sink.event(event)
    writeFileSync(join(d, 'events.jsonl'), 'x'.repeat(EVENTS_MAX_BYTES + 1))
    sink.event(event)

    expect(readFileSync(join(d, 'events.jsonl.1'), 'utf8')).toHaveLength(EVENTS_MAX_BYTES + 1)
    expect(readFileSync(join(d, 'events.jsonl'), 'utf8').trim().split('\n')).toHaveLength(1)
  })

  it('announces a problem state once per 24 hours', () => {
    const d = dir()
    let clock = Date.parse('2026-11-17T10:00:00Z')
    const sink = fileStatusSink(d, () => clock)

    expect(sink.announce('a:skipped-drift:x')).toBe(true)
    expect(sink.announce('a:skipped-drift:x')).toBe(false)
    expect(sink.announce('b:skipped-drift:x')).toBe(true)
    clock += NOTIFY_WINDOW_MS + 1
    expect(sink.announce('a:skipped-drift:x')).toBe(true)
  })

  it('never throws on an unwritable home', () => {
    const sink = fileStatusSink('/proc/definitely/not/writable/tui-slots')

    expect(() => {
      sink.snapshot([row])
      sink.event(event)
      sink.announce('k')
    }).not.toThrow()
  })
})
