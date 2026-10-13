import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React, { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { TextInput } from '../components/textInput.js'

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

// Option+Backspace as a kitty-protocol terminal sends it (CSI 127 ; 3 u) and
// as xterm modifyOtherKeys sends it. hermes-ink names both 'backspace' and,
// for these extended forms, also hands that name to `input`.
const OPT_BACKSPACE = ['\x1b[127;3u', '\x1b[27;3;127~']

describe.each(OPT_BACKSPACE)('Option+Backspace %j on an empty composer', sequence => {
  it('stays empty instead of typing the key name', async () => {
    const stdin = new FakeInput()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const changes: string[] = []

    Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
    Object.assign(stderr, { columns: 80, isTTY: false, rows: 24 })

    function Harness() {
      const [value, setValue] = useState('foo')

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

    // First press deletes "foo"; second and third hit the empty composer.
    stdin.send(sequence)
    await settle(25)
    stdin.send(sequence)
    await settle(25)
    stdin.send(sequence)
    await settle(25)

    instance.unmount()
    instance.cleanup()

    expect(changes.at(0)).toBe('')
    expect(changes.filter(c => c !== '')).toEqual([])
  })
})
