import { describe, expect, it } from 'vitest'

import { clipboardImageCommands, type ClipboardImageRunner, readClipboardImagePng } from '../lib/clipboard.js'

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3])

describe('clipboardImageCommands', () => {
  it('macOS is osascript PNGf alone', () => {
    expect(clipboardImageCommands('darwin', {})).toEqual([['osascript', ['-e', 'the clipboard as «class PNGf»']]])
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

describe('readClipboardImagePng', () => {
  const withRunner = (impl: ClipboardImageRunner) => ((
    platform: NodeJS.Platform = 'darwin',
    env: NodeJS.ProcessEnv = {}
  ) => readClipboardImagePng(platform, env, impl)) as typeof readClipboardImagePng

  it('returns the PNG bytes when the first command yields them', async () => {
    const run = withRunner(async (_cmd, _args) => PNG)
    expect((await run('darwin', {}))?.equals(PNG)).toBe(true)
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

  it('garbage output and total failure are null', async () => {
    expect(await withRunner(async () => Buffer.from('not a png'))()).toBeNull()
    expect(await withRunner(async () => {
      throw new Error('no tool')
    })()).toBeNull()
  })
})
