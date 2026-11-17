import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  chordMatches,
  composerDraftText,
  type KeybindHost,
  parseChord,
  resolveKeybinds,
  runKeybind,
  typedDirectives
} from '../app/keybinds.js'
import { getOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { ActiveWidgetSlot, dispatchWidgetInput } from '../sdk/host.js'
import type { WidgetInput } from '../sdk/types.js'

const press = (key: Partial<Record<'ctrl' | 'meta' | 'shift', boolean>>, ch: string) => ({
  ch,
  key: { ctrl: false, meta: false, shift: false, ...key }
})

const matches = (spec: string, p: ReturnType<typeof press>) => chordMatches(parseChord(spec)!, p.key, p.ch)

describe('chords', () => {
  it('parses alt/ctrl (plus optional shift) and one letter or digit, with the usual aliases', () => {
    expect(parseChord('alt+s')).toEqual({ alt: true, ctrl: false, key: 's', shift: false })
    expect(parseChord('Meta+Shift+S')).toEqual({ alt: true, ctrl: false, key: 's', shift: true })
    expect(parseChord('option+1')).toEqual({ alt: true, ctrl: false, key: '1', shift: false })
    expect(parseChord('control+k')).toMatchObject({ alt: false, ctrl: true })
  })

  it('refuses chords that would be typing or are not understood', () => {
    for (const spec of ['s', 'shift+s', 'alt+', 'alt+ss', 'alt+enter', 'hyper+s', 'alt+-', '']) {
      expect(parseChord(spec), spec).toBeNull()
    }
  })

  // Every encoding of Alt+Shift+S arrives as a capital S, with or without the shift flag.
  it('Alt+S and Alt+Shift+S are different chords, whichever way the terminal encodes the shift', () => {
    expect(matches('alt+s', press({ meta: true }, 's'))).toBe(true)
    expect(matches('alt+s', press({ meta: true, shift: true }, 'S'))).toBe(false)
    expect(matches('alt+s', press({ meta: true }, 'S'))).toBe(false)
    expect(matches('alt+shift+s', press({ meta: true, shift: true }, 'S'))).toBe(true)
    expect(matches('alt+shift+s', press({ meta: true, shift: true }, 's'))).toBe(true)
    expect(matches('alt+shift+s', press({ meta: true }, 'S'))).toBe(true)
    expect(matches('alt+shift+s', press({ meta: true }, 's'))).toBe(false)
  })

  it('needs the exact modifiers: no alt means no match, and ctrl+alt is neither chord', () => {
    expect(matches('alt+s', press({}, 's'))).toBe(false)
    expect(matches('alt+s', press({ ctrl: true, meta: true }, 's'))).toBe(false)
    expect(matches('ctrl+k', press({ ctrl: true }, 'k'))).toBe(true)
    expect(matches('alt+g', press({ meta: true }, 's'))).toBe(false)
  })
})

describe('resolving the table', () => {
  it('layers the user over the plugin defaults, by chord, whatever the spelling', () => {
    const table = resolveKeybinds(
      { 'alt+s': '/stash toggle', 'alt+shift+s': '/stash --global toggle' },
      { 'meta+s': '/other' }
    )

    expect(table.map(b => b.command).sort()).toEqual(['other', 'stash --global toggle'])
  })

  it('an empty command switches a default off; junk entries are dropped', () => {
    const table = resolveKeybinds(
      { 'alt+s': 'stash toggle', 'alt+x': 'x' },
      { 'alt+s': '', 'alt+x': 7, nonsense: '/y', 'alt+z': '/z' }
    )

    expect(table.map(b => b.command)).toEqual(['z'])
  })

  it('works with neither layer', () => {
    expect(resolveKeybinds()).toEqual([])
  })
})

describe('composerDraftText', () => {
  it('joins the accepted lines and the line being typed the way submit does', () => {
    expect(composerDraftText(['first', 'second'], 'third')).toBe('first\nsecond\nthird')
    expect(composerDraftText([], 'only line')).toBe('only line')
  })

  it('keeps a trailing empty line, so a continuation draft loses no line break', () => {
    expect(composerDraftText(['first'], '')).toBe('first\n')
  })
})

// A composer whose edits move the revision, and a host that records what the directives did to it.
function setup(answers: Record<string, unknown>, composer = { text: 'draft', attachments: false }) {
  const state = { ...composer, revision: 1, sid: 'sid-1' as null | string }
  const said: string[] = []
  const sent: { command: string; sid: string; composerText?: string }[] = []

  const edit = (text: string) => {
    state.text = text
    state.revision++
  }

  const host: KeybindHost = {
    clear: vi.fn(() => edit('')),
    composer: { attachments: () => state.attachments, revision: () => state.revision, text: () => state.text },
    replace: vi.fn(edit),
    request: vi.fn(async (command: string, sid: string, composerText?: string) => {
      sent.push({ command, sid, composerText })
      const answer = answers[command]

      if (answer instanceof Error) {
        throw answer
      }

      return answer as never
    }),
    say: (text: string) => void said.push(text),
    sid: () => state.sid
  }

  return { host, said, sent, state }
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0))

describe('running a chord', () => {
  beforeEach(resetOverlayState)

  it('hands the command the live composer text and the session it was pressed in', async () => {
    const { host, sent } = setup({ 'stash toggle': { output: 'ok' } }, { text: 'a\nb', attachments: false })
    runKeybind('/stash toggle', host)
    await settle()

    expect(sent).toEqual([{ command: 'stash toggle', sid: 'sid-1', composerText: 'a\nb' }])
  })

  it('offers no text while an image is attached: text cannot carry it', async () => {
    const { host, sent } = setup({ cmd: { output: 'ok' } }, { text: '[[ Image 1 ]] hi', attachments: true })
    runKeybind('cmd', host)
    await settle()

    expect(sent[0]!.composerText).toBeUndefined()
  })

  it('says so, and runs nothing, without a session', async () => {
    const { host, said, sent, state } = setup({})
    state.sid = null
    runKeybind('cmd', host)
    await settle()

    expect(sent).toEqual([])
    expect(said).toEqual(['keybind: no active session'])
  })

  it('ignores a second press of the same chord on the same text while the first is answering', async () => {
    const { host, sent } = setup({ cmd: { output: 'ok' } })
    runKeybind('cmd', host)
    runKeybind('cmd', host)
    await settle()
    runKeybind('cmd', host)
    await settle()

    expect(sent).toHaveLength(2)
  })

  it('shows a plain answer as a line and leaves the composer alone', async () => {
    const { host, said, state } = setup({ cmd: { output: 'stash full (9)' } })
    runKeybind('cmd', host)
    await settle()

    expect(said).toEqual(['stash full (9)'])
    expect(state.text).toBe('draft')
  })

  it('says why when the command fails, keeping the draft', async () => {
    const { host, said, state } = setup({ cmd: new Error('boom') })
    runKeybind('cmd', host)
    await settle()

    expect(said[0]).toContain('keybind /cmd')
    expect(state.text).toBe('draft')
  })

  it('reports once everything has answered', async () => {
    const { host } = setup({ cmd: { output: 'ok' } })
    const done = vi.fn()
    runKeybind('cmd', host, done)
    expect(done).not.toHaveBeenCalled()
    await settle()

    expect(done).toHaveBeenCalledTimes(1)
  })
})

describe('composer-clear', () => {
  it('shows the notice and clears an untouched composer', async () => {
    const { host, said, state } = setup({ save: { type: 'composer-clear', notice: 'saved' } })
    runKeybind('save', host)
    await settle()

    expect(said).toEqual(['saved'])
    expect(state.text).toBe('')
  })

  it('keeps text typed while the command was answering, and says nothing was applied', async () => {
    const { host, said, state } = setup({ save: { type: 'composer-clear', notice: 'saved' } })
    const slow = host.request

    host.request = async (...args) => {
      state.text = 'draft and more'
      state.revision++

      return slow(...args)
    }

    runKeybind('save', host)
    await settle()

    expect(state.text).toBe('draft and more')
    expect(host.clear).not.toHaveBeenCalled()
    expect(said.at(-1)).toContain('composer changed')
  })

  it('does not clear the composer of another session after a switch', async () => {
    const { host, state } = setup({ save: { type: 'composer-clear' } })
    const slow = host.request

    host.request = async (...args) => {
      state.sid = 'sid-2'

      return slow(...args)
    }

    runKeybind('save', host)
    await settle()

    expect(host.clear).not.toHaveBeenCalled()
    expect(state.text).toBe('draft')
  })

  it('does not clear while an image has since been attached', async () => {
    const { host, state } = setup({ save: { type: 'composer-clear' } })
    const slow = host.request

    host.request = async (...args) => {
      state.attachments = true

      return slow(...args)
    }

    runKeybind('save', host)
    await settle()

    expect(host.clear).not.toHaveBeenCalled()
  })
})

describe('prefill', () => {
  it('replaces the composer, then runs its ack, and shows what the ack says', async () => {
    const { host, said, sent, state } = setup(
      {
        restore: { type: 'prefill', message: 'old draft', notice: 'restored', ack: '/stash applied 1' },
        'stash applied 1': { output: 'stash (0 left)' }
      },
      { text: '', attachments: false }
    )

    runKeybind('restore', host)
    await settle()

    expect(state.text).toBe('old draft')
    expect(said).toEqual(['restored', 'stash (0 left)'])
    expect(sent.map(s => s.command)).toEqual(['restore', 'stash applied 1'])
    expect((host.replace as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0]).toBeLessThan(
      (host.request as ReturnType<typeof vi.fn>).mock.invocationCallOrder[1]!
    )
  })

  it('leaves the stored entry alone (no ack) when the composer changed meanwhile', async () => {
    const { host, sent, state } = setup(
      { restore: { type: 'prefill', message: 'old', ack: 'applied' } },
      { text: '', attachments: false }
    )

    const slow = host.request

    host.request = async (...args) => {
      state.text = 'typed'
      state.revision++

      return slow(...args)
    }

    runKeybind('restore', host)
    await settle()

    expect(state.text).toBe('typed')
    expect(sent.map(s => s.command)).toEqual(['restore'])
  })

  it('tells when the ack fails: the draft is already in the composer', async () => {
    const { host, said, state } = setup(
      { restore: { type: 'prefill', message: 'old', ack: 'applied' }, applied: new Error('gone') },
      { text: '', attachments: false }
    )

    runKeybind('restore', host)
    await settle()

    expect(state.text).toBe('old')
    expect(said.at(-1)).toContain('applied')
  })
})

// A picker acts from its rendered component, so one must be on screen.
let screen: ReturnType<typeof renderSync> | null = null

describe('choices', () => {
  beforeEach(() => {
    resetOverlayState()
    const stdout = Object.assign(new PassThrough(), { columns: 90, rows: 15, isTTY: false })

    screen = renderSync(React.createElement(ActiveWidgetSlot), {
      stdin: new PassThrough() as NodeJS.ReadStream,
      stdout: stdout as NodeJS.WriteStream,
      stderr: new PassThrough() as NodeJS.WriteStream,
      patchConsole: false
    })
  })

  afterEach(() => {
    screen?.unmount()
    screen?.cleanup()
    resetOverlayState()
  })

  const key = (ch: string, value: Partial<WidgetInput['key']> = {}): WidgetInput => ({ ch, key: value }) as WidgetInput

  const rows = [
    { label: 'first', select: '/pick 1', drop: '/drop 1' },
    { label: 'second', select: '/pick 2' }
  ]

  it('opens a picker whose Enter runs the row command through the same directive handling', async () => {
    const { host, state } = setup(
      {
        open: { type: 'choices', title: 'Stash', items: rows, selected: 0 },
        'pick 1': { type: 'prefill', message: 'first draft' }
      },
      { text: '', attachments: false }
    )

    runKeybind('open', host)
    await settle()
    expect(getOverlayState().widget?.appId).toBe('choice-picker')
    dispatchWidgetInput(key('', { return: true }))
    await vi.waitFor(() => expect(state.text).toBe('first draft'))

    expect(getOverlayState().widget).toBeNull()
  })

  it('applies a pick only if the composer is still what it was when the chord was pressed', async () => {
    const { host, said, state } = setup(
      { open: { type: 'choices', title: 'Stash', items: rows }, 'pick 1': { type: 'prefill', message: 'first draft' } },
      { text: '', attachments: false }
    )

    runKeybind('open', host)
    await settle()
    state.sid = 'sid-2'
    dispatchWidgetInput(key('1'))
    await vi.waitFor(() => expect(said.at(-1)).toContain('composer changed'))

    expect(host.replace).not.toHaveBeenCalled()
  })

  it('x runs the row drop command, and its answer may open the list again', async () => {
    const { host, said, sent } = setup({
      open: { type: 'choices', title: 'Stash', items: rows },
      'drop 1': { type: 'choices', title: 'Stash', notice: 'stash (2 left)', items: [rows[1]] }
    })

    runKeybind('open', host)
    await settle()
    dispatchWidgetInput(key('x'))
    await vi.waitFor(() => expect(sent.map(s => s.command)).toEqual(['open', 'drop 1']))
    await vi.waitFor(() => expect(said).toEqual(['stash (2 left)']))
    await vi.waitFor(() => expect(getOverlayState().widget?.appId).toBe('choice-picker'))
  })

  it('Esc runs nothing', async () => {
    const { host, sent } = setup({ open: { type: 'choices', title: 'Stash', items: rows } })
    runKeybind('open', host)
    await settle()
    dispatchWidgetInput(key('', { escape: true }))

    expect(getOverlayState().widget).toBeNull()
    expect(sent).toHaveLength(1)
  })
})

describe('directives outside a chord', () => {
  const gw = { request: vi.fn() } as never

  it('a typed command answering composer-clear clears the composer while its flight is current', () => {
    const setInput = vi.fn()
    typedDirectives(gw, setInput, vi.fn(), 'sid-1', () => false).apply({ type: 'composer-clear' })

    expect(setInput).toHaveBeenCalledExactlyOnceWith('')
  })

  it('and leaves it alone once the flight went stale (another command, another session)', () => {
    const setInput = vi.fn()
    const say = vi.fn()
    typedDirectives(gw, setInput, say, 'sid-1', () => true).apply({ type: 'composer-clear' })

    expect(setInput).not.toHaveBeenCalled()
    expect(say).toHaveBeenCalledWith(expect.stringContaining('composer changed'))
  })
})
