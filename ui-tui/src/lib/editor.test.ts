import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, join } from 'node:path'

import { beforeEach, describe, expect, it } from 'vitest'

import { draftFromEditorText, resolveEditor } from './editor.js'

const exe = (dir: string, name: string): string => {
  const path = join(dir, name)

  writeFileSync(path, '#!/bin/sh\nexit 0\n')
  chmodSync(path, 0o755)

  return path
}

describe('resolveEditor', () => {
  let dir: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'editor-test-'))
  })

  it('honors $VISUAL above all else', () => {
    expect(resolveEditor({ EDITOR: 'vim', PATH: dir, VISUAL: 'helix' })).toEqual(['helix'])
  })

  it('falls back to $EDITOR when $VISUAL is unset', () => {
    expect(resolveEditor({ EDITOR: 'nvim', PATH: dir })).toEqual(['nvim'])
  })

  it('shell-tokenizes editors with arguments', () => {
    expect(resolveEditor({ EDITOR: 'code --wait', PATH: dir })).toEqual(['code', '--wait'])
    expect(resolveEditor({ PATH: dir, VISUAL: 'emacsclient -t' })).toEqual(['emacsclient', '-t'])
  })

  it('ignores whitespace-only env vars', () => {
    const expected = exe(dir, 'editor')

    expect(resolveEditor({ EDITOR: '   ', PATH: dir, VISUAL: '' }, 'linux')).toEqual([expected])
  })

  it('prefers `editor` over nano over vi on $PATH', () => {
    exe(dir, 'nano')
    exe(dir, 'vi')
    const expected = exe(dir, 'editor')

    expect(resolveEditor({ PATH: dir }, 'linux')).toEqual([expected])
  })

  it('falls back to nano before vi when both exist', () => {
    exe(dir, 'vi')
    const expected = exe(dir, 'nano')

    expect(resolveEditor({ PATH: dir }, 'linux')).toEqual([expected])
  })

  it('returns ["vi"] when $PATH is empty', () => {
    expect(resolveEditor({ PATH: '' }, 'linux')).toEqual(['vi'])
  })

  it('walks multi-entry $PATH', () => {
    const a = mkdtempSync(join(tmpdir(), 'editor-a-'))
    const b = mkdtempSync(join(tmpdir(), 'editor-b-'))
    const expected = exe(b, 'editor')

    expect(resolveEditor({ PATH: [a, b].join(delimiter) }, 'linux')).toEqual([expected])
  })

  it('uses notepad.exe on Windows when no env override', () => {
    expect(resolveEditor({ PATH: dir }, 'win32')).toEqual(['notepad.exe'])
  })
})

describe('draftFromEditorText', () => {
  it('puts the last line in the input and the rest above it, as the composer holds a draft', () => {
    expect(draftFromEditorText('one\ntwo\nthree\n')).toEqual({ buf: ['one', 'two'], line: 'three' })
  })

  it('keeps a one-line prompt as the input alone', () => {
    expect(draftFromEditorText('hello\n')).toEqual({ buf: [], line: 'hello' })
  })

  it('drops only trailing whitespace, keeping blank lines inside', () => {
    expect(draftFromEditorText('a\n\nb\n\n')).toEqual({ buf: ['a', ''], line: 'b' })
  })

  it('gives an empty draft for an empty save', () => {
    expect(draftFromEditorText('')).toEqual({ buf: [], line: '' })
    expect(draftFromEditorText('\n\n')).toEqual({ buf: [], line: '' })
  })

  it('round-trips with how the composer flattens a draft', () => {
    const text = 'x\ny\nz'
    const { buf, line } = draftFromEditorText(text)

    expect([...buf, line].join('\n')).toBe(text)
  })
})
