import { writeFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import {
  type ClipboardFileReader,
  clipboardImageCommands,
  type ClipboardImageRunner,
  readClipboardImagePng
} from '../lib/clipboard.js'

// A minimally valid PNG: signature + body + the IEND chunk tail (type + fixed CRC) the
// validator requires — signature-only fixtures are rejected on purpose now.
const PNG = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  Buffer.from('body'),
  Buffer.from([0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82])
])

describe('clipboardImageCommands', () => {
  it('macOS and Windows use no stdout command chain (macOS writes bytes to a file)', () => {
    expect(clipboardImageCommands('darwin', {})).toEqual([])
    expect(clipboardImageCommands('win32', {})).toEqual([])
  })

  it('Wayland tries wl-paste first; plain X11 tries xclip first; the other is the fallback', () => {
    expect(clipboardImageCommands('linux', { XDG_SESSION_TYPE: 'wayland' })[0]![0]).toBe('wl-paste')
    expect(clipboardImageCommands('linux', { WAYLAND_DISPLAY: 'wayland-0' })[0]![0]).toBe('wl-paste')
    expect(clipboardImageCommands('linux', { XDG_SESSION_TYPE: 'x11' })[0]![0]).toBe('xclip')
    expect(clipboardImageCommands('linux', {})).toEqual([
      ['xclip', ['-selection', 'clipboard', '-t', 'image/png', '-o']],
      ['wl-paste', ['--type', 'image/png']]
    ])
  })
})

// macOS: osascript cannot put the pasteboard PNG on stdout — it prints the «data PNGf…»
// hex TEXT. The extraction hands the bytes to AppleScript to WRITE, then reads the file.
// The old stdout-based read could therefore never validate a PNG on a real Mac.
describe('readClipboardImagePng on darwin', () => {
  const run = (impl: ClipboardImageRunner) =>
    ((
      readFileBuffer: ClipboardFileReader,
      platform: NodeJS.Platform = 'darwin',
      env: NodeJS.ProcessEnv = {}
    ) => readClipboardImagePng(platform, env, impl, readFileBuffer)) as typeof readClipboardImagePng

  const fileWithPng: ClipboardFileReader = async path => {
    writeFileSync(path, PNG)

    return PNG
  }

  it('reads the bytes the AppleScript wrote, ignoring osascript stdout', async () => {
    // stdout carries the hex-text representation a real Mac prints — it must not be parsed.
    const osascriptHexText = Buffer.from('«data PNGf89504E470D0A1A0A…»')
    expect(await run(async () => osascriptHexText)(fileWithPng)).not.toBeNull()
  })

  it('returns null when the script reports no pasteboard image ("fail")', async () => {
    let read: string | null = null
    expect(
      await run(async () => Buffer.from('fail'))(async path => {
        read = path

        return PNG
      })
    ).toBeNull()
    expect(read).toBeNull() // the file is not even consulted
  })

  it('returns null when the written file is not a PNG, or osascript fails', async () => {
    expect(await run(async () => Buffer.alloc(0))(async () => Buffer.from('not a png'))).toBeNull()
    expect(
      await run(async () => {
        throw new Error('osascript: execution error')
      })(fileWithPng)
    ).toBeNull()
  })

  it('hands the script a file under a private temp dir the bytes are written to', async () => {
    let script = ''
    await run(async (_cmd, args) => {
      script = args.join(' ')

      return Buffer.alloc(0)
    })(fileWithPng)

    expect(script).toContain('write imgData to f')
    expect(script).toContain('open for access POSIX file')
  })
})

describe('readClipboardImagePng on linux', () => {
  const withRunner = (impl: ClipboardImageRunner) =>
    ((platform: NodeJS.Platform = 'linux', env: NodeJS.ProcessEnv = {}) =>
      readClipboardImagePng(platform, env, impl)) as typeof readClipboardImagePng

  it('returns the PNG bytes when the first command yields them', async () => {
    const run = withRunner(async (_cmd, _args) => PNG)
    expect((await run('darwin' === process.platform ? 'linux' : 'linux', {}))?.equals(PNG)).toBe(true)
  })

  it('falls through a failing command to the next, and validates the PNG signature', async () => {
    let calls = 0

    const run = withRunner(async () => {
      calls += 1

      if (calls === 1) {
        throw new Error('xclip: no image')
      }

      return Buffer.concat([PNG, Buffer.from('\n')])
    })

    expect((await run('linux', { XDG_SESSION_TYPE: 'x11' }))?.equals(PNG)).toBe(true)
    expect(calls).toBe(2)
  })

  it('garbage, signature-only truncation, and total failure are null', async () => {
    expect(await withRunner(async () => Buffer.from('not a png'))()).toBeNull()
    expect(
      await withRunner(async () => Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]))()
    ).toBeNull()
    expect(await withRunner(async () => {
      throw new Error('no tool')
    })()).toBeNull()
  })
})
