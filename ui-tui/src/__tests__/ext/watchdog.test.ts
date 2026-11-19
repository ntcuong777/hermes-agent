import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { startWatchdog } from '../../sdk/ext/watchdog.js'

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const spin = (ms: number) => {
  const end = Date.now() + ms

  while (Date.now() < end) {
    // blocks this thread, like an extension stuck in a loop
  }
}

const rig = () => {
  const statePath = join(mkdtempSync(join(tmpdir(), 'ext-wd-')), 'tui-extensions.json')

  return { statePath, wd: startWatchdog({ hangMs: 120, kill: false, pollMs: 20, statePath }) }
}

describe('watchdog', () => {
  it('records the extension that blocked the thread past hangMs', async () => {
    const { statePath, wd } = rig()

    wd.enter('demo/hello')
    spin(600)
    wd.leave()
    await sleep(100)
    wd.stop()

    expect(JSON.parse(readFileSync(statePath, 'utf8'))).toEqual({
      disabled: { 'demo/hello': 'hung the TUI for over 120ms (watchdog)' }
    })
  })

  it('leaves a call that finishes in time alone, and a thread that is merely idle', async () => {
    const { statePath, wd } = rig()

    wd.enter('demo/fast')
    spin(30)
    wd.leave()
    await sleep(250)
    wd.stop()

    expect(existsSync(statePath)).toBe(false)
  })

  it('keeps what the file already holds', async () => {
    const statePath = join(mkdtempSync(join(tmpdir(), 'ext-wd-')), 'tui-extensions.json')

    writeFileSync(statePath, JSON.stringify({ disabled: { 'old/one': 'earlier' } }))

    const wd = startWatchdog({ hangMs: 100, kill: false, pollMs: 20, statePath })

    wd.enter('new/one')
    spin(600)
    wd.leave()
    await sleep(100)
    wd.stop()

    expect(JSON.parse(readFileSync(statePath, 'utf8')).disabled).toEqual({
      'new/one': 'hung the TUI for over 100ms (watchdog)',
      'old/one': 'earlier'
    })
  })
})
