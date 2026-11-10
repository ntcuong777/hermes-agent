import { describe, expect, it } from 'vitest'

import { isPasteHotkey } from '../components/textInput.js'

const key = (over: Partial<{ ctrl: boolean; meta: boolean; super: boolean; shift: boolean }> = {}) => ({
  ctrl: false,
  meta: false,
  shift: false,
  super: undefined,
  ...over
})

describe('isPasteHotkey', () => {
  it('matches the C0 Ctrl+V byte any terminal sends', () => {
    expect(isPasteHotkey('\x16', 'v', key({ ctrl: true }), false)).toBe(true)
  })

  it('matches Alt+V raw sequences', () => {
    expect(isPasteHotkey('\x1bv', 'v', key(), false)).toBe(true)
    expect(isPasteHotkey('\x1bV', 'V', key(), false)).toBe(true)
  })

  it('matches Ctrl+V delivered by extended-key protocols (kitty CSI u / tmux extended-keys)', () => {
    // tmux extended-keys delivers ESC [ 118 ; 5 u; the parsed key carries the
    // binding name 'v' with ctrl set — the raw sequence matches no literal.
    expect(isPasteHotkey('\x1b[118;5u', 'v', key({ ctrl: true }), false)).toBe(true)
    expect(isPasteHotkey('\x1b[118;5u', 'v', key({ ctrl: true }), true)).toBe(true)
  })

  it('matches Cmd+V on macOS only', () => {
    expect(isPasteHotkey('\x1b[118;1u', 'v', key({ super: true }), true)).toBe(true)
    expect(isPasteHotkey('\x1b[118;1u', 'v', key({ super: true }), false)).toBe(false)
  })

  it('does not fire on plain typing or other ctrl chords', () => {
    expect(isPasteHotkey('v', 'v', key(), false)).toBe(false)
    expect(isPasteHotkey('V', 'V', key({ shift: true }), false)).toBe(false)
    expect(isPasteHotkey('\x03', 'c', key({ ctrl: true }), false)).toBe(false)
  })
})
