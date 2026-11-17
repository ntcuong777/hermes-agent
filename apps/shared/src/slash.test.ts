import { describe, expect, it } from 'vitest'

import { looksLikeSlashCommand, parseCommandDispatch, parseSlashCommand } from './slash'

describe('parseSlashCommand', () => {
  it('splits the name off a single separator and lower-cases it', () => {
    expect(parseSlashCommand('/cron add daily')).toEqual({ arg: 'add daily', name: 'cron' })
    expect(parseSlashCommand('/Exit ')).toEqual({ arg: '', name: 'exit' })
  })

  it('keeps a multi-line argument byte-for-byte (#41323, #55510)', () => {
    const arg = 'first line\nsecond line\n\n  indented tail'

    expect(parseSlashCommand(`/pr-triage ${arg}`)).toEqual({ arg, name: 'pr-triage' })
    expect(parseSlashCommand('/goal ship   it').arg).toBe('ship   it')
  })

  it('takes the name across a newline boundary like the CLI and gateway (split on any whitespace)', () => {
    expect(parseSlashCommand('/goal\npasted block')).toEqual({ arg: 'pasted block', name: 'goal' })
  })

  it('treats a bare or space-separated slash as no command (CLI parity)', () => {
    expect(parseSlashCommand('/')).toEqual({ arg: '', name: '' })
    expect(parseSlashCommand('/   ')).toEqual({ arg: '', name: '' })
    expect(parseSlashCommand('/ some words')).toEqual({ arg: '', name: '' })
  })

  it('recognises a command only at position 0 with a single bare segment', () => {
    expect(looksLikeSlashCommand('/help')).toBe(true)
    expect(looksLikeSlashCommand('/goal do it')).toBe(true)
    expect(looksLikeSlashCommand('/usr/local/bin')).toBe(false)
    expect(looksLikeSlashCommand('run /clean')).toBe(false)
  })
})

describe('parseCommandDispatch', () => {
  it('parses every variant of the command.dispatch union and keeps notice/display', () => {
    expect(parseCommandDispatch({ type: 'exec', output: 'hi' })).toEqual({ type: 'exec', output: 'hi' })
    expect(parseCommandDispatch({ type: 'plugin' })).toEqual({ type: 'plugin', output: undefined })
    expect(parseCommandDispatch({ type: 'alias', target: 'help' })).toEqual({ type: 'alias', target: 'help' })
    expect(parseCommandDispatch({ type: 'skill', name: 'x', message: 'do', display: '/x' })).toEqual({
      type: 'skill',
      name: 'x',
      message: 'do',
      display: '/x'
    })
    // /goal set answers {type:send, notice, message}; dropping the notice once
    // made /goal look like it did nothing on the desktop.
    expect(parseCommandDispatch({ type: 'send', notice: '⊙ Goal set', message: 'do the thing' })).toEqual({
      type: 'send',
      message: 'do the thing',
      notice: '⊙ Goal set',
      display: undefined
    })
    expect(parseCommandDispatch({ type: 'prefill', message: 'edit me', notice: '↶ rewound' })).toEqual({
      type: 'prefill',
      ack: undefined,
      message: 'edit me',
      notice: '↶ rewound'
    })
    // A prefill may name the command to run once the composer really holds the message.
    expect(parseCommandDispatch({ type: 'prefill', message: 'm', ack: '/stash applied 1' })).toMatchObject({
      ack: '/stash applied 1'
    })
    // A keybind's save confirmation: the only response that clears the composer, so a plain
    // string ("stash full") keeps the draft.
    expect(parseCommandDispatch({ type: 'composer-clear', notice: '📋 Stashed' })).toEqual({
      type: 'composer-clear',
      notice: '📋 Stashed'
    })
    expect(
      parseCommandDispatch({
        type: 'choices',
        title: 'Stash',
        selected: 1,
        items: [
          { label: 'a', select: '/s 1', drop: '/d 1' },
          { label: 'b', select: '/s 2' }
        ]
      })
    ).toEqual({
      type: 'choices',
      title: 'Stash',
      selected: 1,
      items: [
        { label: 'a', select: '/s 1', drop: '/d 1' },
        { label: 'b', select: '/s 2', drop: undefined }
      ]
    })
  })

  it('rejects malformed payloads', () => {
    expect(parseCommandDispatch(null)).toBeNull()
    expect(parseCommandDispatch([{ type: 'exec' }])).toBeNull()
    expect(parseCommandDispatch({ type: 'alias' })).toBeNull()
    expect(parseCommandDispatch({ type: 'skill', name: 1 })).toBeNull()
    expect(parseCommandDispatch({ type: 'send' })).toBeNull()
    expect(parseCommandDispatch({ type: 'send', message: 42 })).toBeNull()
    expect(parseCommandDispatch({ type: 'prefill', notice: 'x' })).toBeNull()
    expect(parseCommandDispatch({ type: 'nope' })).toBeNull()
    expect(parseCommandDispatch({ type: 'stash-saved' })).toBeNull()
    // A picker needs a title and at least one row that names a command to run.
    expect(parseCommandDispatch({ type: 'choices', items: [{ label: 'a', select: '/s' }] })).toBeNull()
    expect(parseCommandDispatch({ type: 'choices', title: 't', items: [] })).toBeNull()
    expect(parseCommandDispatch({ type: 'choices', title: 't', items: [{ label: 'a' }] })).toBeNull()
  })
})
