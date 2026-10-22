import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React, { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { TextInput } from '../components/textInput.js'

// B26: Alt+S ran the stash action AND typed "s" ("atmsss" after three presses). The insert
// branch of TextInput used its own condition that never looked at `meta`, so any Alt+letter
// the composer does not bind fell through and was typed. The typed key also bumped the
// composer revision, which made the stash-saved clear (guarded by that revision) skip.
class FakeInput extends EventEmitter {
  chunks: string[] = []
  isRaw = false
  isTTY = true
  readableLength = 0

  read() {
    const next = this.chunks.shift() ?? null
    this.readableLength = this.chunks.length

    return next
  }

  ref = vi.fn()

  send(...chunks: string[]) {
    this.chunks.push(...chunks)
    this.readableLength = this.chunks.length
    this.emit('readable')
  }

  setEncoding = vi.fn()

  setRawMode = vi.fn((enabled: boolean) => {
    this.isRaw = enabled
  })

  unref = vi.fn()
}

const settle = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms))

async function typed(sequence: string, start: string) {
  const stdin = new FakeInput()
  const stdout = new PassThrough()
  const stderr = new PassThrough()
  const changes: string[] = []

  Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
  Object.assign(stderr, { columns: 80, isTTY: false, rows: 24 })

  function Harness() {
    const [value, setValue] = useState(start)

    return (
      <TextInput
        columns={80}
        onChange={next => {
          changes.push(next)
          setValue(next)
        }}
        onSubmit={() => {}}
        value={value}
      />
    )
  }

  const instance = renderSync(React.createElement(Harness), {
    patchConsole: false,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })

  await settle()
  stdin.send(sequence)
  await settle(40)
  instance.unmount()
  instance.cleanup()

  return changes
}

// ESC s (xterm/Terminal.app/iTerm), kitty CSI u, xterm modifyOtherKeys.
describe.each(['\x1bs', '\x1b[115;3u', '\x1b[27;3;115~'])('Alt+S %j', sequence => {
  it('does not type "s" into a composer with text', async () => {
    expect(await typed(sequence, 'draft')).toEqual([])
  })

  it('does not type "s" into an empty composer', async () => {
    expect(await typed(sequence, '')).toEqual([])
  })
})

it('a plain s is still typed', async () => {
  expect(await typed('s', 'draft')).toEqual(['drafts'])
})
